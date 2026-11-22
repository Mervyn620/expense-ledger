using System.Text.Json;
using Expenses.Desktop;
static JsonElement Jobs(params object[] jobs) => JsonSerializer.SerializeToElement(jobs);
static object Job(string id, string kind, string status) => new {id,kind,status,label="fixture"};
int checks = 0;
void Check(bool ok, string name) { if (!ok) throw new Exception(name); checks++; Console.WriteLine("PASS " + name); }
var tracker = new DesktopJobTracker();
Check(tracker.Observe(Jobs(Job("old","refresh:stores","passed"),Job("current","refresh:stores","running"))).Count==0,"startup does not replay old completions");
var completed=tracker.Observe(Jobs(Job("current","refresh:stores","partial"),Job("old","refresh:stores","passed")));
Check(completed.Count==1 && completed[0].ChangesLedger,"running task becoming partial updates ledger");
Check(tracker.Observe(Jobs(Job("current","refresh:stores","partial"))).Count==0,"duplicate polls do not refresh twice");
Check(!tracker.Observe(Jobs(Job("validate","validate","passed")))[0].ChangesLedger,"validation does not reload ledger pages");
Check(!tracker.Observe(Jobs(Job("login","refresh:scotia_login","passed")))[0].ChangesLedger,"login preparation does not reload data");
Check(tracker.Observe(Jobs(Job("failed","refresh:stores","failed")))[0].ChangesLedger,"failed refresh can have imported partial data");
Check(tracker.Observe(Jobs(Job("scotia","refresh:scotia","passed")))[0].ChangesLedger,"bank reconciliation refreshes cash and payment data");
Check(tracker.Observe(Jobs(Job("fast","refresh:local","passed")))[0].ChangesLedger,"fast completion between polls is detected");
Check(tracker.Observe(Jobs(Job("queued","refresh:stores","queued"),Job("","refresh:stores","passed"))).Count==0,"queued and malformed rows stay inert");
Console.WriteLine($"{checks} checks passed");
