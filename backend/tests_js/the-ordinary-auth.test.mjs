import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyOrdinaryEntryState,
  classifyOrdinaryAuthFacts,
  isOrdinaryOrdersLocation,
  ordinaryAuthFactsExpression,
  ordinaryGoogleClickExpression,
} from "../scripts/collectors/the-ordinary-auth.mjs";

const eligible = {
  host_allowed: true,
  login_page: true,
  google_button_count: 1,
  google_url_allowed: true,
  challenge_visible: false,
};

test("The Ordinary permits only one exact same-site Google recovery click", () => {
  assert.equal(classifyOrdinaryAuthFacts(eligible), "auto_google_once");
  assert.equal(
    classifyOrdinaryAuthFacts({ ...eligible, google_button_count: 2 }),
    "manual_login_required",
  );
  assert.equal(
    classifyOrdinaryAuthFacts({ ...eligible, google_url_allowed: false }),
    "manual_login_required",
  );
  assert.equal(
    classifyOrdinaryAuthFacts({ ...eligible, challenge_visible: true }),
    "manual_login_required",
  );
});

test("The Ordinary skips Google recovery outside its own login page", () => {
  assert.equal(
    classifyOrdinaryAuthFacts({ ...eligible, login_page: false }),
    "not_login",
  );
  assert.equal(
    isOrdinaryOrdersLocation({ host_allowed: true, path: "/en-ca/orders" }),
    true,
  );
  assert.equal(
    isOrdinaryOrdersLocation({ host_allowed: false, path: "/en-ca/orders" }),
    false,
  );
});

test("The Ordinary recovery expressions never read credential stores", () => {
  const source = ordinaryAuthFactsExpression() + ordinaryGoogleClickExpression();
  assert.match(source, /a\.oauth-google\[data-url\]/);
  assert.match(source, /SocialLogin-OAuthLogin/);
  assert.doesNotMatch(source, /document\.cookie|localStorage|sessionStorage/);
  assert.doesNotMatch(source, /input\[type=.password/i);
  assert.doesNotMatch(source, /, \[data-sitekey\]/);
});

test("The Ordinary waits for a late login redirect to finish rendering", () => {
  assert.equal(classifyOrdinaryEntryState({
    host_allowed: true,
    login_page: false,
    path: "/en-ca/orders",
  }, { orders: [], empty_signal: false }), "waiting");
  assert.equal(classifyOrdinaryEntryState({
    host_allowed: true,
    login_page: true,
    path: "/en-ca/login",
    google_button_count: 0,
    challenge_visible: false,
  }, { orders: [], empty_signal: false }), "waiting");
  assert.equal(classifyOrdinaryEntryState({
    host_allowed: true,
    login_page: true,
    path: "/en-ca/login",
    google_button_count: 1,
    challenge_visible: false,
  }, { orders: [], empty_signal: false }), "login_ready");
});
