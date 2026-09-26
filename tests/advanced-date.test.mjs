import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const app=fs.readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('../web/index.html',import.meta.url),'utf8');
const assignment=app.match(/\$\('#advanced-deal-as-of'\)\.textContent=[^;]+;/)?.[0];
function render(metrics){
  const span={textContent:'待载入'};
  vm.runInNewContext(assignment,{data:{meta:{metrics}},$:selector=>{
    assert.equal(selector,'#advanced-deal-as-of');return span;
  }});
  return span.textContent;
}
test('advanced filter date uses the current bootstrap deal_as_of, not the legacy cutoff',()=>{
  assert.ok(assignment,'A dynamic render must be wired to the date span');
  assert.equal(render({deal_as_of:'2026-08-09'}),'2026-08-09');
  assert.equal(render({deal_as_of:'2026-08-10'}),'2026-08-10');
  assert.ok(app.indexOf(assignment)>app.indexOf("data=await get('/api/bootstrap')"));
  assert.doesNotMatch(html,/成交截至 2026-06-23/);
});
test('missing dates remain unknown and never borrow a build or observation date',()=>{
  for(const deal_as_of of [null,undefined,''])assert.equal(render({deal_as_of,built_at:'2026-09-09'}),'未知');
});
test('advanced filter keeps same-record budget and same-year school relation caveats',()=>{
  assert.ok(html.includes('预算与面积需同一条价格记录满足。查询学校时，按所选年度关联住宅线索筛选学校；无年度关系则不满足。候选估算不是单套报价。'));
  assert.ok(html.includes('已入库成交记录截至 <span id="advanced-deal-as-of">待载入</span>（含缺价记录，不代表当前行情）。'));
});
test('community comparison reuses the graduated relation plan instead of showing a dead-end no-relation cell',()=>{
  assert.match(app,/const schoolLinks=x=>/);
  assert.match(app,/plan\.items\.map\(r=>/);
  assert.match(app,/同区核验入口/);
  assert.match(app,/state\.admission==='all'\?'全部类型':state\.admission/);
  assert.doesNotMatch(app,/没有 \$\{esc\(state\.year\)\} 年名单关系，未知/);
});
