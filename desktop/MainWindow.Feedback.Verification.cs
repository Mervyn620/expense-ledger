using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.UI;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingFeedback;
    static double FeedbackContrast(Color a,Color b)
    {
        double L(Color c) {
            double S(byte value){var s=value/255.0;return s<=.04045?s/12.92:Math.Pow((s+.055)/1.055,2.4);}
            return .2126*S(c.R)+.7152*S(c.G)+.0722*S(c.B);
        }
        var x=L(a);var y=L(b);return (Math.Max(x,y)+.05)/(Math.Min(x,y)+.05);
    }
    async Task VerifyFeedbackAsync()
    {
        if(verifyingFeedback || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_FEEDBACK")!="1")return;
        verifyingFeedback=true;
        var checks=new List<string>();var profiles=new List<object>();string? failure=null;
        var before=JsonSerializer.Serialize(preferences);
        var originalAppearance=preferences.Appearance.Normalize();var originalMode=preferences.VisualMode;
        string? originalWeb=null;ContentDialog? dialog=null;
        var captureStart=capturedWindowFrames.Count;var skipStart=skippedWindowFrames.Count;
        void Check(bool condition,string name){if(!condition)throw new Exception(name);checks.Add(name);}
        async Task Capture(string name){FocusLayoutVerificationWindow();await Task.Delay(250);CaptureWindowPixels("feedback-0.13.1-"+name);}
        try {
            ResumeWeb();
            for(var i=0;i<100 && await browser.CoreWebView2.ExecuteScriptAsync("!!window.LedgerAppearance && window.expenseDesktopReady===true")!="true";i++)await Task.Delay(100);
            originalWeb=await browser.CoreWebView2.ExecuteScriptAsync("localStorage.getItem('expense-ledger-appearance-v1')");
            foreach(var profile in new[]{("ink","static"),("jade","static"),("cloud","static"),("indigo","static"),("ink","particle")}) {
                await NavigateAsync("operations");ResumeWeb();
                await browser.CoreWebView2.ExecuteScriptAsync("document.querySelector('[data-color-choice=auto]').click();document.querySelector('[data-wallpaper-choice="+profile.Item1+"]').click();");
                await SyncAppearanceFromWebAsync();
                preferences.VisualMode=profile.Item2;ApplyNativeTheme();await SyncVisualModeToWebAsync();await Task.Delay(200);
                var name=profile.Item1+"-"+profile.Item2;
                var text=((SolidColorBrush)layout.Resources["LedgerTextBrush"]).Color;
                var fill=((SolidColorBrush)layout.Resources["LedgerNoticeBackgroundBrush"]).Color;
                var ratio=FeedbackContrast(text,fill);
                Check(ratio>=4.5,"native_notice_text_contrast_"+name);
                var sample=await browser.CoreWebView2.ExecuteScriptAsync("""
                (()=>{const t=document.getElementById('toast');const samples=[];
                  const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
                  const ctx=canvas.getContext('2d');
                  function rgb(value){ctx.clearRect(0,0,1,1);ctx.fillStyle=value;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3);}
                  function lum(v){return v.map(n=>{n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4}).reduce((s,n,i)=>s+n*[.2126,.7152,.0722][i],0);}
                  for(const severity of ['','error','warning']){t.className='toast '+severity;t.hidden=false;t.textContent='验证提示：请先登录对应平台，再重试。';const s=getComputedStyle(t);const a=lum(rgb(s.color)),b=lum(rgb(s.backgroundColor));samples.push({severity,color:s.color,background:s.backgroundColor,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)});}
                  t.className='toast error';return samples;
                })()
                """);
                using(var parsed=JsonDocument.Parse(sample)) {
                    foreach(var row in parsed.RootElement.EnumerateArray())Check(row.GetProperty("ratio").GetDouble()>=4.5,"web_feedback_"+name+"_"+row.GetProperty("severity").GetString());
                    profiles.Add(new{profile=name,nativeContrast=ratio,text=text.ToString(),fill=fill.ToString(),web=parsed.RootElement.Clone()});
                }
                if(profile.Item2=="static" && profile.Item1 is "ink" or "jade")await Capture(name+"-web");
                await browser.CoreWebView2.ExecuteScriptAsync("document.getElementById('toast').hidden=true");
                await NavigateAsync("native-maintenance");
                var notice=new InfoBar{Title="暂时未能完成",Message="验证提示：请先登录对应平台，再重试。",Severity=InfoBarSeverity.Error,IsOpen=true,IsClosable=false};
                ConfigureLedgerNotice(notice);
                var content=new StackPanel{Spacing=16};content.Children.Add(new TextBlock{Text="这是合成错误提示，不会启动采集或修改账本。",TextWrapping=TextWrapping.Wrap});content.Children.Add(notice);
                dialog=new ContentDialog{XamlRoot=layout.XamlRoot,Title="任务暂时未能完成",Content=content,PrimaryButtonText="重试",CloseButtonText="关闭"};ConfigureLedgerDialog(dialog);
                var showing=dialog.ShowAsync();await Task.Delay(300);
                Check(dialog.RequestedTheme==layout.ActualTheme && ReferenceEquals(dialog.Background,SurfaceGlass),"dialog_theme_and_surface_"+name);
                var title=FindGlassElement(notice,"Title") as TextBlock;var messageText=FindGlassElement(notice,"Message") as TextBlock;
                Check(title?.Foreground is SolidColorBrush titleBrush && titleBrush.Color==text && messageText?.Foreground is SolidColorBrush messageBrush && messageBrush.Color==text,"actual_infobar_title_and_message_"+name);
                await Capture(name+"-native");
                dialog.Hide();await showing;dialog=null;
            }
            ApplyAppearanceVisuals(true,true);
            Check(FeedbackContrast(((SolidColorBrush)layout.Resources["LedgerTextBrush"]).Color,((SolidColorBrush)layout.Resources["LedgerNoticeBackgroundBrush"]).Color)>=4.5,"high_contrast_notice_fallback_parameter_branch");
            preferences.Appearance=originalAppearance;preferences.VisualMode=originalMode;ApplyNativeTheme();
            ApplyAppearanceVisuals(false,true);
            await NavigateAsync("operations");ResumeWeb();
            ShowWebError("网页暂时未能加载","这是合成网页故障，原生页面仍可使用。");await Task.Delay(200);
            Check(webErrorPanel.Visibility==Visibility.Visible,"web_failure_panel_visible_with_glass");await Capture("web-failure");
            webFailed=false;pageReady=true;ShowRoute();
            ShowError("账本暂时无法连接","这是合成连接错误，原有账本数据保持不变。");await Task.Delay(200);
            Check(ReferenceEquals(loadingCard.Background,SurfaceGlass)&&loading.Visibility==Visibility.Visible,"connection_error_has_paired_text_and_glass_surface");await Capture("connection-failure");
            Check(connectionFailed && loading.Visibility==Visibility.Visible && browser.Visibility==Visibility.Collapsed,"connection_error_survives_window_activation");
        } catch(Exception error){failure=error.Message;Log(error);}
        finally {
            dialog?.Hide();
            connectionFailed=false;loading.Visibility=Visibility.Collapsed;webFailed=false;pageReady=true;
            preferences.Appearance=originalAppearance;preferences.VisualMode=originalMode;ApplyNativeTheme();ApplyAppearanceVisuals();
            await NavigateAsync("operations");ResumeWeb();
            if(originalWeb!=null)await browser.CoreWebView2.ExecuteScriptAsync("(()=>{const value="+originalWeb+";if(value===null)localStorage.removeItem('expense-ledger-appearance-v1');else localStorage.setItem('expense-ledger-appearance-v1',value);window.dispatchEvent(new StorageEvent('storage',{key:'expense-ledger-appearance-v1'}));document.getElementById('toast').hidden=true;})()");
            preferences.Appearance=originalAppearance;preferences.VisualMode=originalMode;ApplyNativeTheme();await SyncVisualModeToWebAsync();preferences.Save(root);await RegisterDesktopBridgeAsync();WriteState("ready");
            if(JsonSerializer.Serialize(preferences)!=before)failure ??= "preferences_not_restored";
            File.WriteAllText(Path.Combine(root,"verification","feedback-0.13.1-runtime.json"),JsonSerializer.Serialize(new{status=failure==null?"passed":"failed",failure,checks,profiles,capturedWindowFrames=capturedWindowFrames.Skip(captureStart),skippedWindowFrames=skippedWindowFrames.Skip(skipStart),scope="Synthetic in-app errors and theme changes; no real collection, bank reconciliation, ledger write or OS high-contrast change."},new JsonSerializerOptions{WriteIndented=true}));
        }
    }
}
