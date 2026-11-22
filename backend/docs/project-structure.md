# 当前项目结构

快照时间：2026-09-11T15:18:18-04:00。项目根目录：`<project root>`。

以下完整展开业务源码、脚本、配置、文档、测试和浏览器扩展；依赖、缓存、备份、账目文件及运行状态在这里按目录折叠。Python 字节码缓存不显示。

包含上述折叠目录内文件的完整名称清单：`../.app-runtime/project-structure-full.txt`。只读取目录和文件名，没有读取账目、配置密钥或浏览器资料内容。链接/目录联接不向外遍历。

```text
expenses/
├── .agents/
├── .app-runtime/ [运行环境或生成数据；见完整目录树]
├── .codex/ [运行环境或生成数据；见完整目录树]
├── .codex-shop-browser/ [运行环境或生成数据；见完整目录树]
├── .git/ [运行环境或生成数据；见完整目录树]
├── .pytest_cache/ [运行环境或生成数据；见完整目录树]
├── app/
│   ├── static/
│   │   ├── icons/
│   │   │   ├── arrow-down-right.svg
│   │   │   ├── arrow-up-right.svg
│   │   │   ├── banknote.svg
│   │   │   ├── calendar-days.svg
│   │   │   ├── chart-no-axes-combined.svg
│   │   │   ├── check-circle-2.svg
│   │   │   ├── chevron-right.svg
│   │   │   ├── circle-alert.svg
│   │   │   ├── credit-card.svg
│   │   │   ├── database.svg
│   │   │   ├── landmark.svg
│   │   │   ├── layout-dashboard.svg
│   │   │   ├── LICENSE-lucide.txt
│   │   │   ├── loader-circle.svg
│   │   │   ├── maximize-2.svg
│   │   │   ├── minimize-2.svg
│   │   │   ├── receipt-text.svg
│   │   │   ├── refresh-cw.svg
│   │   │   ├── search.svg
│   │   │   ├── shield-check.svg
│   │   │   ├── store.svg
│   │   │   ├── wallet-cards.svg
│   │   │   ├── x.svg
│   │   │   ├── zoom-in.svg
│   │   │   └── zoom-out.svg
│   │   ├── analysis.css
│   │   ├── analysis.html
│   │   ├── analysis.js
│   │   ├── app.js
│   │   ├── index.html
│   │   ├── motion.css
│   │   ├── price-watch.css
│   │   ├── price-watch.html
│   │   ├── price-watch.js
│   │   ├── styles.css
│   │   └── trend.js
│   ├── __init__.py
│   ├── analysis.py
│   ├── analysis_answers.py
│   ├── analysis_cache.py
│   ├── collector_backends.py
│   ├── collector_recovery.py
│   ├── data_status.py
│   ├── jobs.py
│   ├── ledger_questions.py
│   ├── main.py
│   ├── repository.py
│   ├── serve.py
│   └── settings.py
├── backups/ [运行环境或生成数据；见完整目录树]
├── config/
│   ├── collector-manifest.schema.json
│   ├── collector_backends.json
│   ├── collectors.json
│   ├── merchant_rules.json
│   ├── payment_cards.json
│   ├── platforms.json
│   ├── price_watch.json
│   └── workflow.json
├── data/ [运行环境或生成数据；见完整目录树]
├── docs/
│   ├── architecture.md
│   ├── collectors.md
│   ├── ledger-reliability.md
│   ├── powerbi.md
│   ├── price-watch.md
│   ├── project-structure.md
│   ├── release-history-evidence.md
│   ├── trend-interaction-plan.md
│   └── ui-layout.md
├── downloads/ [运行环境或生成数据；见完整目录树]
├── exports/ [运行环境或生成数据；见完整目录树]
├── local_extensions/
│   └── walmart-invoice-exporter-ca/
│       ├── _locales/
│       │   └── en/
│       │       └── messages.json
│       ├── images/
│       │   ├── icon128.png
│       │   ├── icon16.png
│       │   ├── icon256.png
│       │   ├── icon32.png
│       │   ├── icon48.png
│       │   └── icon512.png
│       ├── background.js
│       ├── CHANGELOG.md
│       ├── content.js
│       ├── exceljs.bare.min.js
│       ├── manifest.json
│       ├── README.md
│       ├── sidepanel.css
│       ├── sidepanel.html
│       ├── sidepanel.js
│       └── utils.js
├── node_modules/ [运行环境或生成数据；见完整目录树]
├── raw/ [运行环境或生成数据；见完整目录树]
├── scripts/
│   ├── browser/
│   │   ├── cdp-click-selector.mjs
│   │   ├── cdp-click-text.mjs
│   │   ├── cdp-client.mjs
│   │   ├── cdp-shop-page.mjs
│   │   ├── collection-policy.mjs
│   │   ├── content-ready.mjs
│   │   ├── ensure-shop-tabs.mjs
│   │   ├── export-visible-page.mjs
│   │   ├── prepare-scotia-login.mjs
│   │   └── sanitize.mjs
│   ├── collectors/
│   │   ├── amazon-order-utils.mjs
│   │   ├── backend_control.py
│   │   ├── collect-walmart-orders.mjs
│   │   ├── csv.mjs
│   │   ├── export-amazon-orders.mjs
│   │   ├── export-fantuan-orders.mjs
│   │   ├── export-payment-evidence.mjs
│   │   ├── export-scotia-transactions.mjs
│   │   ├── export-the-ordinary-orders.mjs
│   │   ├── export-uber-eats-receipts.mjs
│   │   ├── fantuan-navigation.mjs
│   │   ├── fantuan-order-utils.mjs
│   │   ├── runner.py
│   │   ├── scotia-transaction-utils.mjs
│   │   ├── the-ordinary-auth.mjs
│   │   ├── the-ordinary-order-utils.mjs
│   │   ├── the-ordinary-tab-selection.mjs
│   │   ├── tls_diagnostics.py
│   │   ├── vps_backend_service.py
│   │   ├── vps_bundle_client.py
│   │   ├── vps_login_session.py
│   │   ├── vps_release_manager.py
│   │   └── walmart-order-utils.mjs
│   ├── importers/
│   │   ├── amazon.py
│   │   ├── fantuan.py
│   │   ├── payment_evidence.py
│   │   ├── the_ordinary.py
│   │   ├── uber_eats.py
│   │   ├── uber_receipt_payments.py
│   │   ├── walmart_cdp.py
│   │   └── walmart_xlsx.py
│   ├── ledger/
│   │   ├── db.py
│   │   ├── payment_assignments.py
│   │   ├── refresh_assignments.py
│   │   ├── runtime_state.py
│   │   ├── schema.py
│   │   └── sync.py
│   ├── lib/
│   │   └── atomic-file.mjs
│   ├── maintenance/
│   │   ├── initialize_workspace.py
│   │   ├── safety.py
│   │   ├── stability_suite.py
│   │   └── validate_all.py
│   ├── price_watch/
│   │   ├── systemd/
│   │   │   ├── expense-price-watch-trigger.service
│   │   │   ├── expense-price-watch-trigger.timer
│   │   │   ├── expense-price-watch.service
│   │   │   └── expense-price-watch.timer
│   │   ├── client.py
│   │   ├── diagnose_store.py
│   │   ├── inspect_my_items.mjs
│   │   ├── local_collector.mjs
│   │   ├── local_probe.mjs
│   │   ├── on_demand.py
│   │   ├── vps_scheduled.py
│   │   └── vps_service.py
│   ├── reconciliation/
│   │   └── scotia.py
│   ├── reporting/
│   │   ├── export_reports.py
│   │   ├── setup_powerbi_odbc.ps1
│   │   ├── status_report.py
│   │   └── validate.py
│   ├── bootstrap-expense-ledger.ps1
│   ├── check-expense-app.ps1
│   ├── configure-deepseek.ps1
│   ├── deploy-vps-collector.ps1
│   ├── export-expense-profile.ps1
│   ├── import-expense-profile.ps1
│   ├── install-expense-app-shortcut.ps1
│   ├── install-price-watch-startup.ps1
│   ├── manage-vps-collector-login.ps1
│   ├── prepare-scotia-login.ps1
│   ├── run-stability-suite.ps1
│   ├── start-expense-app.ps1
│   ├── start-price-watch-receiver.ps1
│   ├── start-shop-browser.ps1
│   ├── stop-expense-app.ps1
│   ├── stop-shop-browser.ps1
│   └── weekly-expense-refresh.ps1
├── tests/
│   ├── test_amazon_import.py
│   ├── test_analysis_answer_validation.py
│   ├── test_analysis_month_scope.py
│   ├── test_collector_backends.py
│   ├── test_collector_manifest.py
│   ├── test_collector_recovery.py
│   ├── test_expense_analysis.py
│   ├── test_expense_app.py
│   ├── test_fantuan_import.py
│   ├── test_import_publications.py
│   ├── test_ledger_evolution.py
│   ├── test_payment_assignments.py
│   ├── test_payment_evidence_import.py
│   ├── test_price_watch_client.py
│   ├── test_price_watch_on_demand.py
│   ├── test_price_watch_schedule.py
│   ├── test_price_watch_vps_service.py
│   ├── test_report_exports.py
│   ├── test_runtime_state.py
│   ├── test_safety.py
│   ├── test_scotia_reconciliation.py
│   ├── test_scotia_stable_orders.py
│   ├── test_sqlite_sync.py
│   ├── test_stability_suite.py
│   ├── test_status_report.py
│   ├── test_the_ordinary_import.py
│   ├── test_tls_diagnostics.py
│   ├── test_trend_series.py
│   ├── test_uber_eats_import.py
│   ├── test_uber_receipt_payment_import.py
│   ├── test_validation_status.py
│   ├── test_version_history.py
│   ├── test_vps_collector_bundle.py
│   ├── test_vps_release_manager.py
│   ├── test_walmart_cdp_import.py
│   └── test_workspace_initialization.py
├── tests_js/
│   ├── amazon-order-utils.test.mjs
│   ├── collection-policy.test.mjs
│   ├── collector-performance.test.mjs
│   ├── fantuan-navigation.test.mjs
│   ├── fantuan-order-utils.test.mjs
│   ├── local-price-watch.test.mjs
│   ├── sanitize.test.mjs
│   ├── scotia-login.test.mjs
│   ├── scotia-transaction-utils.test.mjs
│   ├── the-ordinary-auth.test.mjs
│   ├── the-ordinary-oauth-lifecycle.test.mjs
│   ├── the-ordinary-order-utils.test.mjs
│   ├── the-ordinary-tab-selection.test.mjs
│   ├── trend-interaction.test.mjs
│   └── walmart-order-utils.test.mjs
├── .gitattributes
├── .gitignore
├── AGENT_HANDOFF.md
├── CHANGELOG.md
├── MAINTENANCE_LOG.md
├── package-lock.json
├── package.json
├── README.md
└── requirements-app.txt
```
