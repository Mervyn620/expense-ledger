import assert from "node:assert/strict";
import test from "node:test";

import {
  fantuanCardSignature,
  parseFantuanCardText,
  fantuanOrderMetadata,
  fantuanCardsNeedingDetails,
  refreshFantuanCachedStatuses,
} from "../scripts/collectors/fantuan-order-utils.mjs";

test("Fantuan card signatures normalize date-time and amount formatting", () => {
  assert.equal(
    fantuanCardSignature({ dateTime: " 2026-08-19   18:30 ", total: "$26.54" }),
    fantuanCardSignature({ datetime: "2026-08-19 18:30", total: 26.54 }),
  );
});

test("Fantuan refreshes a cached order when the live card becomes cancelled", () => {
  const existing = [{
    order_id: "fantuan-1",
    datetime: "2026-08-19 18:30",
    total: 26.54,
    status: "进行中",
    merchant: "荣华甜苑",
  }];

  const refreshed = refreshFantuanCachedStatuses(existing, [{
    dateTime: "2026-08-19 18:30",
    total: "$26.54",
    status: "已取消",
  }]);

  assert.deepEqual(refreshed, [{
    order_id: "fantuan-1",
    datetime: "2026-08-19 18:30",
    total: 26.54,
    status: "已取消",
    merchant: "荣华甜苑",
  }]);
  assert.equal(existing[0].status, "进行中");
});

test("Fantuan retains cached status when the card has no status or no match", () => {
  const existing = [{
    order_id: "fantuan-1",
    datetime: "2026-08-19 18:30",
    total: 26.54,
    status: "已完成",
  }];

  assert.strictEqual(
    refreshFantuanCachedStatuses(existing, [{
      dateTime: "2026-08-19 18:30",
      total: 26.54,
      status: "",
    }])[0],
    existing[0],
  );
  assert.strictEqual(
    refreshFantuanCachedStatuses(existing, [{
      dateTime: "2026-08-20 18:30",
      total: 26.54,
      status: "已取消",
    }])[0],
    existing[0],
  );
});

test("active Fantuan cards without amount or date remain discoverable",()=>{
 const card=parseFantuanCardText("Fixture Restaurant\n取餐号 #12345\n等待商家接单","active");
 assert.equal(card.kind,"active");assert.equal(card.pickupNumber,"#12345");
 assert.equal(card.status,"等待商家接单");assert.equal(card.date,"");
});
test("active and scheduled orders use the purchase time from detail instead of delivery day",()=>{
 const metadata=fantuanOrderMetadata({sn:"fixture-order",orderInfo:{contentList:[{type:"CREATED_AT",name:"2026-10-02 00:32"}]},orderStatusInfo:{statusString:"预约订单"}},{dateTime:"2026-10-03 12:00",kind:"active"});
 assert.deepEqual(metadata,{orderId:"fixture-order",date:"2026-10-02",dateTime:"2026-10-02 00:32",status:"预约订单"});
 assert.throws(()=>fantuanOrderMetadata({sn:"fixture"},{kind:"active"}),/purchase time/);
 assert.throws(()=>fantuanOrderMetadata({orderInfo:{contentList:[{type:"CREATED_AT",name:"2026-10-02 00:32"}]}},{}),/stable order ID/);
});
test("cached active orders refresh through completion while settled history remains cached",()=>{
 const previous=[{datetime:"2026-10-01 12:00",total:20,status:"配送中"},{datetime:"2026-09-30 12:00",total:25,status:"已完成"}];
 const active={kind:"active",status:"等待商家接单"};
 const finished={dateTime:"2026-10-01 12:00",total:20,status:"已完成"};
 const settled={dateTime:"2026-09-30 12:00",total:25,status:"已完成"};
 assert.deepEqual(fantuanCardsNeedingDetails(previous,[active,finished,settled]),[active,finished]);
});
