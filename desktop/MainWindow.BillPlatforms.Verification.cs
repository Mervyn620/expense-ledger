using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.Web.WebView2.Core;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingBillPlatforms;
    async Task VerifyBillPlatformsAsync()
    {
        if (verifyingBillPlatforms || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_BILLS") != "1") return;
        verifyingBillPlatforms=true;
        var checks=new List<string>();var samples=new List<object>();string? failure=null;
        ContentDialog? dialog=null;
        var captureStart=capturedWindowFrames.Count;var skipStart=skippedWindowFrames.Count;
        var original=JsonSerializer.Serialize(preferences);
        void Check(bool condition,string label){if(!condition)throw new Exception(label);checks.Add(label);}
        try {
            await NavigateAsync("operations");ResumeWeb();
            for(var i=0;i<100 && await browser.CoreWebView2.ExecuteScriptAsync("!!window.LedgerBillRefresh && window.expenseDesktopReady===true")!="true";i++)await Task.Delay(100);
            Check(await browser.CoreWebView2.ExecuteScriptAsync("document.querySelectorAll('.update-surface .operation-action').length===2 && document.querySelectorAll('[data-scotia-action]').length===1")=="true","one_bill_entry_and_scotia_preserved");
            await browser.CoreWebView2.ExecuteScriptAsync("document.getElementById('billRefreshButton').click()");
            var json=await browser.CoreWebView2.ExecuteScriptAsync("(()=>{const d=document.getElementById('billRefreshDialog'),f=d.querySelector('fieldset'),s=getComputedStyle(d);return {open:d.open,selected:[...d.querySelectorAll('input:checked')].map(i=>i.value),scroll:f.scrollHeight>f.clientHeight,overflow:document.documentElement.scrollWidth>innerWidth,color:s.color,fill:s.backgroundColor,blur:s.backdropFilter,theme:document.documentElement.dataset.theme};})()");
            using(var doc=JsonDocument.Parse(json)) {
                var value=doc.RootElement;
                Check(value.GetProperty("open").GetBoolean(),"web_dialog_opens");
                Check(value.GetProperty("selected").EnumerateArray().Select(item=>item.GetString()).SequenceEqual(preferences.DefaultBillPlatforms),"web_selection_matches_saved_native_defaults");
                Check(value.GetProperty("scroll").GetBoolean()&&!value.GetProperty("overflow").GetBoolean(),"web_platform_list_scrolls_without_page_overflow");
                samples.Add(value.Clone());
            }
            await Task.Delay(200);
            var filePath=Path.Combine(root,"verification","bill-platforms-0.13.0-web.png");File.WriteAllBytes(filePath,Array.Empty<byte>());
            var file=await Windows.Storage.StorageFile.GetFileFromPathAsync(filePath);
            using(var stream=await file.OpenAsync(Windows.Storage.FileAccessMode.ReadWrite))await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream);
            FocusLayoutVerificationWindow();await Task.Delay(250);CaptureWindowPixels("bills-0.13.0-web");
            await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('#billRefreshDialog input[value=tnt_ca]').checked=true;document.getElementById('billRefreshDialog').close();document.getElementById('billRefreshButton').click();");
            Check(await browser.CoreWebView2.ExecuteScriptAsync("[...document.querySelectorAll('#billRefreshDialog input:checked')].map(i=>i.value)")==JsonSerializer.Serialize(preferences.DefaultBillPlatforms),"temporary_platform_choice_resets_on_reopen");
            await browser.CoreWebView2.ExecuteScriptAsync("document.getElementById('billRefreshDialog').close()");
            await NavigateAsync("native-settings");
            Check(defaultBillPlatforms.Visibility==Visibility.Visible,"native_settings_has_single_default_platform_button");
            dialog=CreateBillPlatformsDialog(out var choices);var showing=dialog.ShowAsync();await Task.Delay(350);
            var scroll=(ScrollViewer)dialog.Content;
            Check(choices.Count==6 && choices.Where(box=>box.IsChecked==true).Select(box=>(string)box.Tag).SequenceEqual(preferences.DefaultBillPlatforms),"native_six_platform_choices_match_defaults");
            Check(scroll.ScrollableHeight>0,"native_small_dialog_can_scroll");
            Check(dialog.RequestedTheme==layout.ActualTheme && ReferenceEquals(dialog.Background,SurfaceGlass),"native_dialog_uses_current_theme_and_glass");
            FocusLayoutVerificationWindow();await Task.Delay(250);CaptureWindowPixels("bills-0.13.0-native");
            await CaptureNativePageAsync("native","bill-platforms-0.13.0",dialog);
            scroll.ChangeView(null,scroll.ScrollableHeight,null,true);await Task.Delay(150);
            Check(scroll.VerticalOffset>0,"native_default_platform_list_scrolls_down");
            dialog.Hide();await showing;dialog=null;
            Check(JsonSerializer.Serialize(preferences)==original,"cancelled_dialog_preserves_all_preferences");
        } catch(Exception error){failure=error.Message;Log(error);}
        finally {
            dialog?.Hide();
            if(browser.CoreWebView2!=null){ResumeWeb();await browser.CoreWebView2.ExecuteScriptAsync("document.getElementById('billRefreshDialog')?.close()");}
            await NavigateAsync("operations");
            File.WriteAllText(Path.Combine(root,"verification","bill-platforms-0.13.0-runtime.json"),JsonSerializer.Serialize(new{status=failure==null?"passed":"failed",failure,checks,samples,capturedWindowFrames=capturedWindowFrames.Skip(captureStart),skippedWindowFrames=skippedWindowFrames.Skip(skipStart),scope="In-app control invocation and actual WebView preview; no collection or financial write is triggered."},new JsonSerializerOptions{WriteIndented=true}));
        }
    }
}
