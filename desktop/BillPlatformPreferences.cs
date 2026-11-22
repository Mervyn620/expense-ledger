using System.Collections.Generic;
using System.Linq;
namespace Expenses.Desktop;
internal static class BillPlatformPreferences
{
    public static readonly string[] All = { "walmart_ca", "uber_eats", "fantuan", "amazon_ca", "the_ordinary_ca", "tnt_ca" };
    public static string[] Defaults() => new[] { "walmart_ca", "fantuan" };
    public static string[] Normalize(IEnumerable<string>? selected) => selected == null ? Defaults() : All.Where(selected.Contains).ToArray();
}
