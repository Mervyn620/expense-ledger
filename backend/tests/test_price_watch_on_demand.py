import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "price_watch" / "on_demand.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("price_watch_on_demand", SCRIPT)
on_demand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(on_demand)


def config():
    return {
        "enabled": True,
        "transport": "ssh",
        "ssh_host": "ledger_vps",
        "store_id": "3055",
        "store_name": "MISSISSAUGA (SQUARE ONE), ON",
        "store_query": "A1A 1A1",
        "home_exit_remote_port": 1080,
        "home_exit_ready_timeout_seconds": 5,
        "collection_timeout_seconds": 60,
        "check_limit": 20,
        "check_delay_seconds": 75,
    }


class FakeTunnel:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class OnDemandPriceWatchTest(unittest.TestCase):
    def test_cli_exposes_only_the_active_local_backend(self):
        self.assertEqual(on_demand.parse_args([]).backend, "local")
        with (
            patch.object(sys, "stderr", io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            on_demand.parse_args(["--backend", "vps"])

    def test_local_backend_uses_local_collector_without_vps_sync_or_tunnel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collected = {
                "contract": on_demand.price_client.CONTRACT,
                "status": "completed",
                "collector_status": "completed",
                "route": "local",
                "store_id": "3055",
                "checked": 2,
                "checks": [],
                "deals": [],
            }
            received = {
                "checks": 2,
                "deals": 1,
                "collector_status": "completed",
            }
            with (
                patch.object(on_demand.price_client, "load_config", return_value=config()),
                patch.object(
                    on_demand,
                    "run_local_collector",
                    return_value=collected,
                ) as local_collect,
                patch.object(
                    on_demand.price_client,
                    "persist_deals_payload",
                    return_value=received,
                ) as persist,
                patch.object(on_demand.price_client, "sync_products") as sync,
                patch.object(on_demand, "start_home_exit_tunnel") as tunnel,
            ):
                result = on_demand.run(root, backend="local")

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["backend"], "local")
            self.assertEqual(result["route"], "local")
            local_collect.assert_called_once()
            persist.assert_called_once_with(
                root.resolve(),
                collected,
                merge_existing=False,
                output_relative_path=on_demand.LOCAL_DEALS_RELATIVE_PATH,
                update_receive_state=False,
            )
            sync.assert_not_called()
            tunnel.assert_not_called()

    def test_vps_backend_uses_temporary_home_exit_and_closes_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actions = []
            tunnel = FakeTunnel()
            final_payload = {
                "contract": on_demand.price_client.CONTRACT,
                "collector_status": "completed",
                "route": "home_exit",
                "checked": 20,
                "deals": [],
                "checks": [],
            }

            def remote(_config, action, **kwargs):
                actions.append(action)
                if action == "run":
                    self.assertEqual(
                        kwargs["payload"]["proxy"],
                        "socks5://127.0.0.1:1080",
                    )
                    return {
                        "contract": on_demand.price_client.CONTRACT,
                        "status": "completed",
                        "route": "home_exit",
                        "checked": 20,
                        "deals": 2,
                    }
                self.assertEqual(action, "deals")
                return final_payload

            with (
                patch.object(on_demand.price_client, "load_config", return_value=config()),
                patch.object(on_demand.price_client, "sync_products") as sync,
                patch.object(
                    on_demand,
                    "start_home_exit_tunnel",
                    return_value=tunnel,
                ),
                patch.object(
                    on_demand,
                    "wait_for_home_exit",
                    return_value="socks5://127.0.0.1:1080",
                ),
                patch.object(
                    on_demand.price_client,
                    "ssh_request_json",
                    side_effect=remote,
                ),
                patch.object(
                    on_demand.price_client,
                    "persist_deals_payload",
                    return_value={
                        "checked": 20,
                        "checks": 20,
                        "deals": 2,
                        "collector_status": "completed",
                    },
                ) as persist,
            ):
                result = on_demand.run(root, backend="vps")

            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["route"], "home_exit")
            self.assertEqual(result["checked"], 20)
            self.assertEqual(result["collector_status"], "completed")
            self.assertEqual(actions, ["run", "deals"])
            self.assertTrue(tunnel.terminated)
            sync.assert_called_once()
            persist.assert_called_once_with(
                root.resolve(),
                final_payload,
                merge_existing=False,
            )
            self.assertEqual(
                sync.call_args.args[0].resolve(),
                root.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
