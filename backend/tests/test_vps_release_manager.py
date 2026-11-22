import ast
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


MANAGER = load_module(
    "vps_release_manager_test",
    ROOT / "scripts" / "collectors" / "vps_release_manager.py",
)
RELEASE_A = "20260812T120000Z-a71711c"
RELEASE_B = "20260812T130000Z-b71711c"


class VpsReleaseBundleTest(unittest.TestCase):
    def test_release_allowlist_tracks_service_required_sources_exactly(self):
        service_path = ROOT / "scripts/collectors/vps_backend_service.py"
        tree = ast.parse(service_path.read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "REQUIRED_SOURCES"
                for target in node.targets
            )
        )
        required = tuple(ast.literal_eval(assignment.value))

        self.assertEqual(MANAGER.REQUIRED_SOURCES, required)
        self.assertEqual(
            set(MANAGER.ALLOWED_FILES),
            {
                "vps_backend_service.py",
                "vps_login_session.py",
                *(f"app/{path}" for path in required),
            },
        )

    def test_release_allowlist_contains_every_relative_javascript_dependency(self):
        required = set(MANAGER.REQUIRED_SOURCES)
        import_pattern = re.compile(
            r'(?:\bfrom\s+|\bimport\s+)["\'](\.{1,2}/[^"\']+)["\']'
        )
        missing = []
        for relative in sorted(required):
            if not relative.endswith(".mjs"):
                continue
            source = ROOT / relative
            for dependency in import_pattern.findall(source.read_text(encoding="utf-8")):
                resolved = (source.parent / dependency).resolve()
                try:
                    dependency_relative = resolved.relative_to(ROOT).as_posix()
                except ValueError:
                    missing.append(f"{relative} -> {dependency} (outside repository)")
                    continue
                if dependency_relative not in required:
                    missing.append(f"{relative} -> {dependency_relative}")

        self.assertEqual(missing, [])

    def test_plan_has_exact_allowlist_and_no_remote_writes(self):
        manifest, contents = MANAGER.build_manifest(
            ROOT,
            RELEASE_A,
            source_commit="a71711c",
        )

        self.assertEqual(set(contents), set(MANAGER.ALLOWED_FILES))
        self.assertEqual(
            {row["path"] for row in manifest["files"]},
            set(MANAGER.ALLOWED_FILES),
        )
        forbidden = " ".join(contents).lower()
        for marker in ("profiles", "runtime/", "data/", "cookie", "sqlite"):
            self.assertNotIn(marker, forbidden)

    def test_bundle_rejects_extra_missing_and_tampered_members(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "release.zip"
            MANAGER.build_bundle(ROOT, bundle, RELEASE_A, "a71711c")
            manifest, contents = MANAGER.read_bundle(bundle)
            self.assertEqual(manifest["release_id"], RELEASE_A)
            self.assertEqual(set(contents), set(MANAGER.ALLOWED_FILES))

            with zipfile.ZipFile(bundle) as source:
                members = {
                    info.filename: source.read(info.filename)
                    for info in source.infolist()
                }
            for name, mutate in (
                ("extra", lambda rows: rows.update({"data/private.sqlite": b"x"})),
                ("missing", lambda rows: rows.pop(next(iter(MANAGER.ALLOWED_FILES)))),
                (
                    "tampered",
                    lambda rows: rows.__setitem__(
                        next(iter(MANAGER.ALLOWED_FILES)),
                        b"tampered",
                    ),
                ),
            ):
                rows = dict(members)
                mutate(rows)
                invalid = Path(temp) / f"{name}.zip"
                with zipfile.ZipFile(invalid, "w") as output:
                    for path, content in rows.items():
                        output.writestr(path, content)
                with self.subTest(name=name):
                    with self.assertRaises(MANAGER.ReleaseError):
                        MANAGER.read_bundle(invalid)

    def test_previous_exact_allowlists_remain_valid_rollback_trees(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest, contents = MANAGER.build_manifest(
                ROOT,
                RELEASE_A,
                source_commit="a71711c",
            )
            for index, allowed in enumerate(
                (
                    MANAGER.PRE_PAYMENT_TEXT_ALLOWED_FILES,
                    MANAGER.PRE_CONTENT_READY_ALLOWED_FILES,
                    MANAGER.PREVIOUS_ALLOWED_FILES,
                    MANAGER.LEGACY_ALLOWED_FILES,
                ),
                start=1,
            ):
                release = Path(temp) / f"release-{index}"
                release.mkdir()
                previous_contents = {
                    path: content for path, content in contents.items()
                    if path in allowed
                }
                previous_manifest = {
                    **manifest,
                    "files": [
                        {
                            "path": path,
                            "size": len(content),
                            "sha256": MANAGER.sha256_bytes(content),
                        }
                        for path, content in sorted(previous_contents.items())
                    ],
                }
                (release / "manifest.json").write_text(
                    json.dumps(previous_manifest),
                    encoding="utf-8",
                )
                for path, content in previous_contents.items():
                    target = release / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)

                validated = MANAGER.validate_release_tree(release)

                self.assertEqual(validated["release_id"], RELEASE_A)

    def test_powershell_plan_is_local_and_machine_readable(self):
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable.")
        probe = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("No source Git history is available.")
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        release_id = f"20260812T120000Z-{head[:12]}"
        script = ROOT / "scripts" / "deploy-vps-collector.ps1"
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Plan",
                "-ReleaseId",
                release_id,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["contract"], MANAGER.PLAN_CONTRACT)
        self.assertFalse(payload["remote_writes"])
        self.assertEqual(payload["file_count"], len(MANAGER.ALLOWED_FILES))

    def test_staged_validation_runs_compile_node_and_manifest_checks(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable.")
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "release.zip"
            stage = Path(temp) / "stage"
            MANAGER.build_bundle(ROOT, bundle, RELEASE_A, "a71711c")
            manifest, contents = MANAGER.read_bundle(bundle)
            MANAGER.write_release_tree(stage, manifest, contents)

            MANAGER.run_staged_validation(
                stage,
                sys.executable,
                node,
                fallback_roots=(ROOT,),
            )

            self.assertEqual(
                MANAGER.validate_release_tree(stage)["release_id"],
                RELEASE_A,
            )
            self.assertFalse((stage / ".validation-workspace").exists())
            self.assertFalse((stage / ".validation-pycache").exists())

    def test_release_id_hash_must_match_manifest_commit(self):
        with self.assertRaisesRegex(MANAGER.ReleaseError, "prefix"):
            MANAGER.build_manifest(ROOT, RELEASE_A, source_commit="b71711c")

    def test_git_source_verification_supports_nested_desktop_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); backend = root / "backend"; backend.mkdir()
            source = backend / "source.py"; source.write_bytes(b"VALUE = 1\n")
            for args in (["init"],["config","user.name","Release Test"],["config","user.email","release@example.invalid"],["config","core.autocrlf","false"],["add","backend/source.py"],["commit","-m","fixture"]):
                subprocess.run(["git","-C",str(root),*args],check=True,capture_output=True)
            head = subprocess.run(["git","-C",str(root),"rev-parse","HEAD"],check=True,capture_output=True,text=True).stdout.strip()
            with patch.object(MANAGER,"source_mapping",return_value={"vps_backend_service.py":source}):
                self.assertEqual(MANAGER.verify_git_source(backend,head,f"20260929T120000Z-{head[:12]}"),head)

    def test_git_source_verification_rejects_dirty_or_non_head_content(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("Git is unavailable.")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.py"
            source.write_bytes(b"VALUE = 1\n")
            for arguments in (
                ["init"],
                ["config", "user.name", "Release Test"],
                ["config", "user.email", "release@example.invalid"],
                ["add", "source.py"],
                ["commit", "-m", "source"],
            ):
                subprocess.run(
                    [git, "-C", str(root), *arguments],
                    check=True,
                    capture_output=True,
                )
            head = subprocess.run(
                [git, "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            release_id = f"20260812T120000Z-{head[:12]}"
            with patch.object(
                MANAGER,
                "source_mapping",
                return_value={"vps_backend_service.py": source},
            ):
                self.assertEqual(
                    MANAGER.verify_git_source(root, head, release_id),
                    head,
                )
                source.write_bytes(b"VALUE = 2\n")
                with self.assertRaisesRegex(MANAGER.ReleaseError, "not clean"):
                    MANAGER.verify_git_source(root, head, release_id)


class VpsReleaseActivationTest(unittest.TestCase):
    def build_bundle(self, root, release_id, commit):
        bundle = root / f"{release_id}.zip"
        MANAGER.build_bundle(ROOT, bundle, release_id, commit)
        return bundle

    def materialize_legacy(self, base):
        for archive_path, source in MANAGER.source_mapping(ROOT).items():
            destination = Path(base) / archive_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)

    def apply_guards(self):
        return (
            patch.object(MANAGER, "login_status_active", return_value=False),
            patch.object(MANAGER, "login_ports_active", return_value=False),
            patch.object(MANAGER, "managed_login_processes", return_value=[]),
        )

    def guarded(self, *patches):
        stack = ExitStack()
        for context in (*patches, *self.apply_guards()):
            stack.enter_context(context)
        return stack

    def test_post_switch_failure_restores_previous_current(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)
            bundle_a = self.build_bundle(Path(temp), RELEASE_A, "a71711c")
            bundle_b = self.build_bundle(Path(temp), RELEASE_B, "b71711c")
            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "probe_current_status", return_value=True),
            ):
                MANAGER.apply_release(base, bundle_a)
            self.assertEqual(MANAGER.current_release_id(base), RELEASE_A)

            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "probe_current_status", side_effect=[False, True]),
            ):
                with self.assertRaisesRegex(MANAGER.ReleaseError, "previous release"):
                    MANAGER.apply_release(base, bundle_b)

            self.assertEqual(MANAGER.current_release_id(base), RELEASE_A)
            self.assertTrue((base / "releases" / RELEASE_B).is_dir())

    def test_explicit_rollback_switches_only_to_verified_release(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)
            bundle_a = self.build_bundle(Path(temp), RELEASE_A, "a71711c")
            bundle_b = self.build_bundle(Path(temp), RELEASE_B, "b71711c")
            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "probe_current_status", return_value=True),
            ):
                MANAGER.apply_release(base, bundle_a)
                MANAGER.apply_release(base, bundle_b)
                result = MANAGER.rollback_release(base, RELEASE_A)

            self.assertEqual(result["action"], "rolled_back")
            self.assertEqual(result["release_id"], RELEASE_A)
            self.assertEqual(MANAGER.current_release_id(base), RELEASE_A)
            with self.assertRaises(MANAGER.ReleaseError):
                MANAGER.rollback_release(base, "../../unsafe")

    def test_first_apply_preserves_exact_legacy_bootstrap_as_previous(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)
            bundle = self.build_bundle(Path(temp), RELEASE_A, "a71711c")
            legacy_hashes = {
                path: MANAGER.sha256_bytes((base / path).read_bytes())
                for path in MANAGER.ALLOWED_FILES
            }
            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "probe_current_status", return_value=True),
            ):
                result = MANAGER.apply_release(base, bundle)

            bootstrap = result["previous_release_id"]
            self.assertTrue(bootstrap)
            self.assertEqual(MANAGER.current_release_id(base), RELEASE_A)
            self.assertEqual(
                MANAGER.pointer_release_id(base, "previous"),
                bootstrap,
            )
            bootstrap_root = base / "releases" / bootstrap
            for path, expected in legacy_hashes.items():
                self.assertEqual(
                    MANAGER.sha256_bytes((bootstrap_root / path).read_bytes()),
                    expected,
                )
            if sys.platform != "win32":
                self.assertEqual(bootstrap_root.stat().st_mode & 0o777, 0o500)
                self.assertTrue(
                    all(
                        path.stat().st_mode & 0o777 == 0o400
                        for path in bootstrap_root.rglob("*")
                        if path.is_file()
                    )
                )

    def test_partial_bootstrap_pointer_failure_restores_no_active_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)
            real_pointer = MANAGER.atomic_pointer

            def fail_previous(root, name, release_id):
                if name == "previous":
                    raise OSError("simulated previous pointer failure")
                return real_pointer(root, name, release_id)

            with (
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "atomic_pointer", side_effect=fail_previous),
            ):
                with self.assertRaisesRegex(OSError, "simulated"):
                    MANAGER.ensure_bootstrap_release(base, sys.executable, "node")

            self.assertFalse((base / "current").exists())
            self.assertFalse((base / "current").is_symlink())
            self.assertFalse((base / "previous").exists())
            self.assertFalse((base / "previous").is_symlink())

    def test_bootstrap_seal_failure_removes_unactivated_release(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)

            def fail_after_partial_seal(release_dir):
                release_dir = Path(release_dir)
                first_file = next(path for path in release_dir.rglob("*") if path.is_file())
                first_file.chmod(0o400)
                release_dir.chmod(0o500)
                raise OSError("simulated seal failure")

            with (
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(
                    MANAGER,
                    "seal_release_tree",
                    side_effect=fail_after_partial_seal,
                ),
            ):
                with self.assertRaisesRegex(OSError, "simulated seal failure"):
                    MANAGER.ensure_bootstrap_release(base, sys.executable, "node")

            self.assertFalse((base / "current").exists())
            self.assertFalse((base / "previous").exists())
            self.assertEqual(list((base / "releases").iterdir()), [])

    def test_new_release_seal_failure_preserves_active_release_and_pointers(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)
            bundle_a = self.build_bundle(Path(temp), RELEASE_A, "a71711c")
            bundle_b = self.build_bundle(Path(temp), RELEASE_B, "b71711c")
            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "probe_current_status", return_value=True),
            ):
                MANAGER.apply_release(base, bundle_a)
            previous_before = MANAGER.pointer_release_id(base, "previous")

            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(
                    MANAGER,
                    "seal_release_tree",
                    side_effect=OSError("simulated seal failure"),
                ),
            ):
                with self.assertRaisesRegex(OSError, "simulated seal failure"):
                    MANAGER.apply_release(base, bundle_b)

            self.assertEqual(MANAGER.current_release_id(base), RELEASE_A)
            self.assertEqual(
                MANAGER.pointer_release_id(base, "previous"),
                previous_before,
            )
            self.assertFalse((base / "releases" / RELEASE_B).exists())

    def test_release_is_moved_out_of_staging_before_it_is_sealed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            (base / "runtime").mkdir(parents=True)
            self.materialize_legacy(base)
            bundle = self.build_bundle(Path(temp), RELEASE_A, "a71711c")
            sealed_paths = []
            real_seal = MANAGER.seal_release_tree

            def observe_seal(path):
                path = Path(path)
                sealed_paths.append(path)
                self.assertEqual(path.parent.resolve(), (base / "releases").resolve())
                self.assertNotIn("staging", path.parts)
                real_seal(path)

            with self.guarded(
                patch.object(MANAGER, "run_staged_validation"),
                patch.object(MANAGER, "probe_current_status", return_value=True),
                patch.object(MANAGER, "seal_release_tree", side_effect=observe_seal),
            ):
                MANAGER.apply_release(base, bundle)

            self.assertEqual(len(sealed_paths), 2)
            self.assertTrue(all(path.is_dir() for path in sealed_paths))

    def test_login_status_guard_checks_legacy_and_current_scripts(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            releases = base / "releases" / RELEASE_A
            releases.mkdir(parents=True)
            legacy = base / "vps_login_session.py"
            current_script = releases / "vps_login_session.py"
            legacy.write_text("", encoding="utf-8")
            current_script.write_text("", encoding="utf-8")
            (base / "current").symlink_to(releases, target_is_directory=True)
            stopped = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"status": "stopped"}),
                stderr="",
            )
            running = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"status": "running"}),
                stderr="",
            )
            with patch.object(
                MANAGER.subprocess,
                "run",
                side_effect=[stopped, running],
            ) as mocked:
                self.assertTrue(MANAGER.login_status_active(base, sys.executable))
            self.assertEqual(mocked.call_count, 2)

    def test_login_guard_fails_closed_on_invalid_state(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            runtime = base / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "login-session.json").write_text("not-json", encoding="utf-8")
            with self.assertRaisesRegex(MANAGER.ReleaseError, "unreadable"):
                with MANAGER.operation_guard(base):
                    pass

    def test_prune_preserves_current_previous_and_newest_five(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            releases = base / "releases"
            releases.mkdir(parents=True)
            identifiers = [
                f"202608{day:02d}T120000Z-{day:07x}"
                for day in range(1, 9)
            ]
            for identifier in identifiers:
                (releases / identifier).mkdir()
            current = identifiers[0]
            previous = identifiers[1]

            removed = MANAGER.prune_releases(
                base,
                current,
                previous_id=previous,
                retain=5,
            )

            self.assertTrue((releases / current).is_dir())
            self.assertTrue((releases / previous).is_dir())
            newest = set(sorted(identifiers, reverse=True)[:5])
            self.assertTrue(all((releases / item).is_dir() for item in newest))
            self.assertEqual(
                set(removed),
                set(identifiers) - newest - {current, previous},
            )

    def test_guard_rejects_live_login_session(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            runtime = base / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "login-session.json").write_text(
                json.dumps({"browser_pid": 123}),
                encoding="utf-8",
            )
            with patch.object(MANAGER, "process_alive", return_value=True):
                with self.assertRaisesRegex(MANAGER.ReleaseError, "login session"):
                    with MANAGER.operation_guard(base):
                        pass

    def test_guard_rejects_collection_lock_contention(self):
        class BusyLock:
            LOCK_EX = 1
            LOCK_NB = 2
            LOCK_UN = 4

            @staticmethod
            def flock(_descriptor, operation):
                if operation == BusyLock.LOCK_EX | BusyLock.LOCK_NB:
                    raise BlockingIOError()

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "collector"
            runtime = base / "runtime"
            runtime.mkdir(parents=True)
            with patch.object(MANAGER, "fcntl", BusyLock):
                with self.assertRaisesRegex(MANAGER.ReleaseError, "collection"):
                    with MANAGER.operation_guard(base):
                        pass


if __name__ == "__main__":
    unittest.main()
