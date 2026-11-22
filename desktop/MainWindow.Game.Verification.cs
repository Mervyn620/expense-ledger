using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.Web.WebView2.Core;
namespace Expenses.Desktop;
public sealed partial class MainWindow
{
    bool verifyingGame;
    async Task VerifyGameModeAsync()
    {
        if(verifyingGame || Environment.GetEnvironmentVariable("EXPENSE_DESKTOP_VERIFY_GAME")!="1") return;
        verifyingGame=true;
        var checks=new List<string>();
        var originalGame=preferences.GameModeEnabled;
        var originalMode=preferences.VisualMode;
        var originalWallpaper=preferences.Appearance.Wallpaper;
        void Check(bool value,string name) { if(!value) throw new Exception(name); checks.Add(name); }
        async Task<bool> Web(string expression) => await browser.CoreWebView2.ExecuteScriptAsync(expression)=="true";
        async Task Until(Func<bool> condition) { for(var i=0;i<150&&!condition();i++) await Task.Delay(100); if(!condition()) throw new Exception("Timed out waiting for page"); }
        const string score="Number(document.getElementById('cosmosShipCount').textContent)+Number(document.getElementById('cosmosStarCount').textContent)";
        const string hit="document.querySelector('.update-surface').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))";
        try {
            await NavigateAsync("native-settings");
            gameMode.IsOn=false;
            Check(!preferences.GameModeEnabled&&!DesktopPreferences.Load(root).GameModeEnabled,"game_switch_off_is_persisted");
            Check(updateNavigation.Visibility==Visibility.Visible&&!openGame.IsEnabled,"disabled_game_keeps_update_navigation");
            await NavigateAsync("game");
            Check(currentRoute=="native-settings","disabled_game_cannot_be_opened");
            await CaptureNativePageAsync("off","game-settings");
            await NavigateAsync("operations"); await Task.Delay(300);
            Check(await Web("!!window.expenseDesktopCosmosWallpaper && !window.expenseDesktopCosmosWallpaper.wantsFrame() && !document.documentElement.classList.contains('cosmos-active')"),"disabled_game_stops_interaction_on_update_page");
            var before=await browser.CoreWebView2.ExecuteScriptAsync(score);
            await browser.CoreWebView2.ExecuteScriptAsync(hit);
            Check(before==await browser.CoreWebView2.ExecuteScriptAsync(score),"disabled_game_does_not_score_keyboard_hits");
            Check(await Web("getComputedStyle(document.querySelector('#expense-desktop-stars')).display === 'block'"),"game_off_preserves_ambient_stars");
            pageReady=false; browser.CoreWebView2.Reload(); await Until(()=>pageReady); await Task.Delay(300);
            Check(await Web("window.__expenseDesktopGameMode === false && !window.expenseDesktopCosmosWallpaper.wantsFrame()"),"disabled_preference_survives_new_document");
            await NavigateAsync("native-settings");
            gameMode.IsOn=true; staticVisualMode.IsChecked=true;
            Check(updateNavigation.Visibility==Visibility.Visible&&openGame.IsEnabled&&DesktopPreferences.Load(root).GameModeEnabled,"game_switch_on_restores_entry_and_persists");
            await NavigateAsync("game"); await Task.Delay(500);
            Check(currentRoute=="operations"&&preferences.VisualMode=="particle"&&preferences.Appearance.Wallpaper==originalWallpaper,"opening_game_selects_stars_and_preserves_wallpaper");
            Check(await Web("window.expenseDesktopCosmosWallpaper.wantsFrame() && document.documentElement.classList.contains('cosmos-active')"),"game_resumes_from_native_settings");
            before=await browser.CoreWebView2.ExecuteScriptAsync(score);
            await browser.CoreWebView2.ExecuteScriptAsync(hit);
            Check(int.Parse(await browser.CoreWebView2.ExecuteScriptAsync(score))==int.Parse(before)+1,"enabled_game_accepts_keyboard_hit");
            var path=Path.Combine(root,"verification","game-enabled-web.png"); File.WriteAllBytes(path,Array.Empty<byte>());
            var file=await Windows.Storage.StorageFile.GetFileFromPathAsync(path);
            using(var stream=await file.OpenAsync(Windows.Storage.FileAccessMode.ReadWrite)) await browser.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream);
            await NavigateAsync("native-settings"); await CaptureNativePageAsync("on","game-settings");
            File.WriteAllText(Path.Combine(root,"verification","game-mode-runtime.json"),JsonSerializer.Serialize(new{status="passed",checks,at=DateTimeOffset.Now}));
        } catch(Exception error) {
            Log(error); File.WriteAllText(Path.Combine(root,"verification","game-mode-runtime.json"),JsonSerializer.Serialize(new{status="failed",checks,error=error.ToString(),at=DateTimeOffset.Now}));
        } finally {
            gameMode.IsOn=originalGame;
            if(originalMode=="particle") particleVisualMode.IsChecked=true; else staticVisualMode.IsChecked=true;
            SavePreferences();
            await NavigateAsync("native-settings");
            WriteState("ready");
        }
    }
}
