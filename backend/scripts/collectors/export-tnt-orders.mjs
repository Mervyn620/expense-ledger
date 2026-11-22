import {readFile} from "node:fs/promises";
import {connectCdp,listTabs,sleep} from "../browser/cdp-client.mjs";
import {CollectionSessionError, observedDates, runWithCollectionPolicy} from "../browser/collection-policy.mjs";
import {atomicWriteFile} from "../lib/atomic-file.mjs";
import {mergeTntOrders,normalizeTntOrder,orderId,unwrap} from "./tnt-order-utils.mjs";
const PLATFORM="tnt_ca", ORDERS_URL="https://www.tntsupermarket.com/eng/order-history";
function args(argv) {
 const result={port:9223,minDate:"2026-05-01",maxOrders:12,out:"data/tnt_orders.json"};
 for(let i=0;i<argv.length;i++) {
  const key={"--port":"port","--min-date":"minDate","--max-orders":"maxOrders","--out":"out"}[argv[i]];
  if(!key || !argv[i+1]) throw new Error("Unknown or incomplete T&T collector argument.");
  result[key]=["port","maxOrders"].includes(key)?Number(argv[++i]):argv[++i];
 }
 if(!Number.isInteger(result.port)||result.port<1||result.port>65535||!Number.isInteger(result.maxOrders)||result.maxOrders<1||result.maxOrders>100||!/^20\d{2}-\d{2}-\d{2}$/.test(result.minDate))throw new Error("T&T collector arguments are invalid.");
 return result;
}
async function json(cdp,expression) {
 const result=await cdp.call("Runtime.evaluate",{expression,returnByValue:true});
 if(result.exceptionDetails)throw new Error("T&T page evaluation failed.");
 return JSON.parse(result.result.value || "{}");
}
async function connect(tab,policy) {
 const responses=[];
 const cdp=await connectCdp(tab.webSocketDebuggerUrl,{callTimeoutMs:policy.config.cdp_call_timeout_ms,onMessage(message){
  if(message.method!=="Network.responseReceived")return;
  const response=message.params.response;
  if(!/^https:\/\/www\.tntsupermarket\.com\/rest\/V1\/xmapi\/(?:app-get-order(?:s|-details)|app-rma-list)(?:\?|$)/.test(response.url))return;
  const url=new URL(response.url);
  responses.push({id:message.params.requestId,path:url.pathname,orderId:url.searchParams.get("orderId"),status:response.status});
 }});
 cdp.responses=responses;
 await cdp.call("Network.enable",{maxResourceBufferSize:10000000,maxTotalBufferSize:20000000});
 await cdp.call("Page.enable");
 return cdp;
}
async function response(cdp,policy,start,predicate) {
 const deadline=Date.now()+20000;
 while(Date.now()<deadline) {
  const found=cdp.responses.slice(start).find(predicate);
  if(found) {
   await policy.handleHttpStatus(found.status,"T&T order response");
   let body;
   try { body=await cdp.call("Network.getResponseBody",{requestId:found.id}); } catch(error) {
    if(!/No resource|No data found/.test(error.message))throw error;
    await sleep(200);continue;
   }
   const payload=JSON.parse(body.base64Encoded?Buffer.from(body.body,"base64").toString():body.body);
   if(payload?.code===401)await policy.handleHttpStatus(401,"T&T session");
   if([403,429].includes(payload?.code))await policy.handleHttpStatus(payload.code,"T&T session");
   unwrap(payload);return payload;
  }
  await sleep(250);
 }
 await policy.inspectPage(cdp);
 const signedOut=await json(cdp,`JSON.stringify({signedOut:[...document.querySelectorAll('[class*="accountTrigger"] a')].some(a=>/sign in|登录|登入/i.test(a.textContent))})`);
 if(signedOut.signedOut){await policy.recordLoginRequired("Sign in to T&T in the dedicated browser.");throw new CollectionSessionError("T&T requires login in the dedicated browser.");}
 throw new Error("No T&T order response captured.");
}
async function navigate(cdp,policy,url,predicate) {
 await policy.pace(); const start=cdp.responses.length;
 await cdp.call("Page.navigate",{url});
 return response(cdp,policy,start,predicate);
}
async function run(options,policy) {
 const tabs=await policy.retry("list dedicated browser",()=>listTabs(options.port));
 let tab=tabs.find(tab=>tab.type==="page"&&/^https:\/\/www\.tntsupermarket\.com\//.test(tab.url));
 if(!tab){const r=await fetch(`http://127.0.0.1:${options.port}/json/new?${encodeURIComponent(ORDERS_URL)}`,{method:"PUT",signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error("Could not open T&T orders.");tab=await r.json();}
 const list=await connect(tab,policy);let detail,detailTab;
 try {
  let previous=[];
  try {const saved=JSON.parse(await readFile(options.out,"utf8"));if(saved.contract!=="expense-tnt-orders/v1")throw new Error("T&T cache contract is invalid.");previous=saved.orders;}catch(error){if(error.code!=="ENOENT")throw error;}
  const pending=new Set(previous.filter(order=>!order.excluded&&(order.fulfillment!=="delivered" || !order.payment)).map(order=>order.order_id));
  let page=await navigate(list,policy,ORDERS_URL,entry=>entry.path.endsWith("app-get-orders"));
  const cards=new Map(),scanned=[];
  // Actual category controls: Unpaid, In-Progress, Completed, Cancelled, Return / Refund.
  // Never click the order-card Cancel/Return buttons.
  for(const category of [1,2,3,4]) {
   const active = await json(list, `JSON.stringify({index:[...document.querySelectorAll('[class*="myOrder-ordersHeader"] > li')].findIndex(e=>e.className.includes("ordersTitleActive"))})`);
   if(category!==1 || active.index!==1) {
    await policy.pace();const start=list.responses.length;
    const clicked=await json(list,`JSON.stringify((()=>{const buttons=[...document.querySelectorAll('[class*="myOrder-ordersHeader"] button')];if(buttons.length!==5)return {clicked:false};buttons[${category}].click();return {clicked:true};})())`);
    if(!clicked.clicked)throw new Error("T&T order category controls changed.");
    page=await response(list,policy,start,entry=>entry.path.endsWith(category===4?"app-rma-list":"app-get-orders"));
   }
   let seen=0;
   for(let round=0;round<20;round++) {
    const data=unwrap(page);
    if(category===4) {
     if(!Array.isArray(data.lists))throw new Error("T&T refund list schema is invalid.");
     data.items=data.lists.map(record=>({entity_id:orderId(record.order_id),created_at:record.created_at}));
    }
    if(!Array.isArray(data.items)||!Number.isInteger(data.page)||!Number.isInteger(data.total_page)||data.total_page<0)throw new Error("T&T order list schema is invalid.");
    for(const item of data.items) {
     const id=orderId(item.entity_id), date=String(item.created_at||"").slice(0,10);
     if(category!==4 && !/^20\d{2}-\d{2}-\d{2}$/.test(date))throw new Error("T&T list order date is invalid.");
     if(category===4||date>=options.minDate||pending.has(id))cards.set(id,{id,date,category});
    }
    seen+=data.items.length;
    if(data.page>=data.total_page||seen>=options.maxOrders)break;
    await policy.pace();const start=list.responses.length;
    const next=await json(list,`JSON.stringify((()=>{const controls=[...document.querySelectorAll('[class*="pagination"] button,[class*="pagination"] a')];const next=controls.find(e=>!e.disabled&&e.getAttribute('aria-disabled')!=='true'&&/^(?:move to the next page|next(?: page)?|下一页|下一頁)$/i.test((e.getAttribute('aria-label')||e.getAttribute('title')||e.textContent).trim()));if(!next)return {clicked:false};next.click();return {clicked:true};})())`);
    if(!next.clicked)throw new Error("T&T pagination requires review.");
    const incoming=await response(list,policy,start,entry=>entry.path.endsWith(category===4?"app-rma-list":"app-get-orders"));
    if(unwrap(incoming).page!==data.page+1)throw new Error("T&T pagination did not advance.");page=incoming;
   }
   scanned.push(category);
  }
  const priority=card=>pending.has(card.id)?0:card.category===1?1:card.category>=3&&previous.some(order=>order.order_id===card.id)?2:3;
  const selected=[...cards.values()].sort((a,b)=>priority(a)-priority(b)||b.date.localeCompare(a.date)).slice(0,options.maxOrders);
  const incoming=[];
  if(selected.length){const r=await fetch(`http://127.0.0.1:${options.port}/json/new?about:blank`,{method:"PUT",signal:AbortSignal.timeout(10000)});if(!r.ok)throw new Error("Could not open T&T detail tab.");detailTab=await r.json();detail=await connect(detailTab,policy);}
  for(const card of selected) {
   const payload=await policy.retry("read T&T order detail",()=>navigate(detail,policy,`https://www.tntsupermarket.com/eng/sales/order/view/order_id/${card.id}?page=1`,entry=>entry.path.endsWith("app-get-order-details")&&entry.orderId===card.id));
   const order=normalizeTntOrder(payload,card.id);
   if(order.date>=options.minDate||previous.some(cached=>cached.order_id===order.order_id))incoming.push(order);
  }
  const merged=mergeTntOrders(previous,incoming);
  await atomicWriteFile(options.out,JSON.stringify({contract:"expense-tnt-orders/v1",platform:PLATFORM,generated_at:new Date().toISOString(),min_date:options.minDate,coverage:"bounded",refreshed:incoming.length,orders:merged},null,2)+"\n");
  const summary={scanned_categories:scanned.length,refreshed:incoming.length,excluded:incoming.filter(order=>order.excluded).length,cached:merged.length};
  summary.observed_dates = observedDates(cards.values());
  console.log(JSON.stringify(summary));return summary;
 } finally {
  detail?.close();list.close();
  if(detailTab)await fetch(`http://127.0.0.1:${options.port}/json/close/${detailTab.id}`,{signal:AbortSignal.timeout(5000)}).catch(()=>{});
 }
}
const options=args(process.argv.slice(2));
runWithCollectionPolicy(PLATFORM,policy=>run(options,policy),{summary:result=>result}).catch(error=>{console.error(error.message);process.exitCode=1;});
