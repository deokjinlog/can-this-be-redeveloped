/**
 * ui_test.mjs — 웹 UI 실동작 회귀 (jsdom)
 *
 * web/index.html 의 판정 로직은 파이썬 엔진의 JS 포팅이라, 파이썬 테스트가 다 통과해도
 * UI 는 조용히 깨질 수 있다. 실제로 그렇게 두 건이 깨져 있었다:
 *   · 계약일·등기일을 넣어도 dualBox 가 침묵 (인가일 미상일 때 안내조차 없었음)
 *   · matchMedia 없는 환경에서 테마 토글이 예외로 죽음
 *
 * 실행:  npm i jsdom && node ui_test.mjs      (jsdom 없으면 건너뜀)
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  console.log("⏭  jsdom 없음 — 건너뜀 (npm i jsdom 후 다시)");
  process.exit(0);
}

const html = fs.readFileSync(path.join(HERE, "web/index.html"), "utf8");
const errs = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(w) {
    w.fetch = () => Promise.reject(new Error("offline"));       // 서버 없이 검증
    w.addEventListener("error", (e) => errs.push(e.error?.stack || e.message));
  },
});
const w = dom.window;
await new Promise((r) => setTimeout(r, 400));

const q = (s) => w.document.querySelector(s);
const txt = (s) => (q(s)?.textContent || "").trim();
const click = (el) => el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
const input = (el) => el.dispatchEvent(new w.Event("input", { bubbles: true }));

let pass = 0, fail = 0;
function check(name, fn) {
  try {
    const msg = fn();
    console.log(`  ✅ ${name}: ${msg ?? ""}`);
    pass++;
  } catch (e) {
    console.log(`  ❌ ${name}: ${e.message}`);
    fail++;
  }
}
const assert = (c, m) => { if (!c) throw new Error(m); };

check("①초기 렌더 — 세 판정이 다 채워진다", () => {
  assert(txt("#comboV") && txt("#comboV") !== "—", "종합 판정 비어 있음");
  assert(q("#reqsA").children.length > 0, "A 요건 항목 없음");
  assert(q("#reqsC").children.length > 0, "C 자격 항목 없음");
  return `${txt("#comboV")} / A ${txt("#pillA")} / C ${txt("#pillC")}`;
});

check("②프리셋 3종이 서로 다른 결론을 낸다", () => {
  const got = {};
  for (const p of ["green", "cash", "hold"]) {
    click(q(`.preset[data-p="${p}"]`));
    got[p] = txt("#comboV");
  }
  assert(got.green !== got.cash && got.cash !== got.hold, `구분 안 됨: ${JSON.stringify(got)}`);
  return Object.entries(got).map(([k, v]) => `${k}=${v}`).join(" · ");
});

check("③1세대 1주택 해제 → 장기보유 예외 불성립 (§39②4호)", () => {
  click(q('.preset[data-p="green"]'));
  const before = txt("#pillC");
  const oh = q("#onehouse");
  oh.checked = false; input(oh);
  const after = txt("#pillC");
  oh.checked = true; input(oh);
  assert(before !== after, `1주택 해제가 C 판정을 안 바꿈 (둘 다 ${before})`);
  return `${before} → ${after}`;
});

check("④상속·이혼 취득 칩 → §39② '양수' 아님으로 즉시 통과", () => {
  const chip = q('.chip[data-c="sangsokchwideuk"]');
  click(chip);
  const after = txt("#pillC");
  const body = q("#reqsC").textContent;
  click(chip);
  assert(after.includes("승계"), `C 판정 예상 밖: ${after}`);
  assert(body.includes("양수"), "근거에 '양수' 설명이 없음");
  return after;
});

check("⑤계약일·등기일 — 인가일을 몰라도 침묵하지 않는다", () => {
  q("#gyeyak").value = "2020-09-01"; input(q("#gyeyak"));
  q("#deunggi").value = "2020-12-01"; input(q("#deunggi"));
  const t = txt("#dualBox");
  assert(t.length > 0, "dualBox 가 비어 있음 (사용자에겐 고장으로 보인다)");
  assert(t.includes("보류") || t.includes("갈립") || t.includes("바꾸지"), `안내 문구 이상: ${t.slice(0, 40)}`);
  return t.slice(0, 46);
});

check("⑥테마 토글 — matchMedia 없는 환경에서도 죽지 않는다", () => {
  click(q("#theme"));
  const th = w.document.documentElement.getAttribute("data-theme");
  assert(th === "dark" || th === "light", `테마가 안 바뀜: ${th}`);
  return th;
});

check("⑦서버가 없어도 조회 버튼이 페이지를 죽이지 않는다", () => {
  click(q("#ad-btn"));
  click(q("#ag-btn"));
  return "fetch 실패해도 렌더 유지";
});

check("⑧손입력만으론 MET 이 안 된다 (T 단독 MET 금지)", () => {
  click(q('.preset[data-p="green"]'));         // 취득 2011 · 거주 2013 이 채워진 상태
  const body = q("#reqsC").textContent;
  assert(/소유 10년/.test(body), "소유 요건 항목이 없음");
  // 손으로 넣은 값이므로 확정되면 안 된다 — 등기부를 요청해야 한다
  assert(/등기부/.test(body), `서류 요청이 없음: ${body.slice(0, 120)}`);
  const dot = [...q("#reqsC").querySelectorAll(".req")]
    .find((e) => e.textContent.includes("소유 10년"))
    ?.querySelector(".dot");
  assert(dot && !dot.classList.contains("MET"),
    `손입력인데 MET: ${dot?.className}`);
  return "손입력 → 확정 안 됨 + 등기부 요청";
});

check("⑨서류 붙여넣기 — 칸·안내·샘플이 다 있다", () => {
  assert(q("#doc-deungi") && q("#doc-chobon") && q("#doc-btn"), "서류 입력 칸 없음");
  // 개인정보 안내는 페이지에 기본으로 박혀 있어야 한다(프리셋이 덮기 전 원문 기준)
  const raw = w.document.documentElement.outerHTML;
  assert(/저장하지 않/.test(raw) && /마스킹/.test(raw), "개인정보 안내 문구가 없음");
  // green 프리셋은 서류 샘플까지 채워야 '통과' 흐름을 보여줄 수 있다
  click(q('.preset[data-p="green"]'));
  assert(q("#doc-deungi").value.includes("갑"), "green 프리셋에 등기부 샘플이 없음");
  assert(q("#doc-chobon").value.includes("전입"), "green 프리셋에 초본 샘플이 없음");
  q("#doc-deungi").value = ""; q("#doc-chobon").value = "";
  click(q("#doc-btn"));                        // 빈 상태로 눌러도 죽지 않아야
  assert(/없습니다/.test(txt("#doc-msg")), txt("#doc-msg"));
  return "칸·안내·샘플 · 빈 입력 방어";
});

check("⑩런타임 오류 0", () => {
  assert(errs.length === 0, `${errs.length}건:\n${errs.slice(0, 3).join("\n")}`);
  return "없음";
});

console.log(`\n${pass}/${pass + fail} 통과`);
process.exit(fail ? 1 : 0);
