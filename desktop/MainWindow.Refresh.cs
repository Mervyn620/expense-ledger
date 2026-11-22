using System.Linq;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool pendingLedgerRefresh, refreshingLedger;
    void InvalidateNativeLedger()
    {
        listValid = false;
        orderFiltersStale = true;
        pendingLedgerRefresh = true;
    }
    async Task RefreshChangedLedgerAsync()
    {
        if (!pendingLedgerRefresh || refreshingLedger || starting || exiting || cashNoteDialog != null || manualWechatDialog != null ||
            ordersBusy || detailRequest != null || financeBusy || currentRoute is not ("orders" or "cash" or "sources")) return;
        refreshingLedger = true;
        pendingLedgerRefresh = false;
        try {
            if (currentRoute == "orders") {
                if (detailOrderId is long id) await OpenOrderDetailAsync(id, preservePosition: true);
                else await LoadOrdersAsync(preservePosition: true);
            } else {
                var route = currentRoute;
                var selectedId = (cashList.SelectedItem as CashWithdrawal)?.BankTransactionPk;
                var scroll = currentRoute == "cash" ? FindScrollViewer(cashList) : sourcesBody;
                var offset = scroll?.VerticalOffset;
                await LoadFinanceAsync();
                if (financeValid && currentRoute == route && route == "cash")
                    cashList.SelectedItem = cashPage?.Rows.FirstOrDefault(x => x.BankTransactionPk == selectedId);
                if (financeValid && currentRoute == route && offset is double y) { financePanel.UpdateLayout(); scroll?.ChangeView(null, y, null, true); }
            }
        } finally { refreshingLedger = false; }
    }
    static ScrollViewer? FindScrollViewer(DependencyObject parent)
    {
        if (parent is ScrollViewer viewer) return viewer;
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++) {
            var child = FindScrollViewer(VisualTreeHelper.GetChild(parent, i));
            if (child != null) return child;
        }
        return null;
    }
}
