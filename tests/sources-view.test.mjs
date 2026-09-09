// Offline DOM-boundary checks only; native dialog/layout QA remains in the browser.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {sourcePage} from '../web/model.js';

const app=fs.readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const css=fs.readFileSync(new URL('../web/app.css',import.meta.url),'utf8');
const sourceCode=app.slice(app.indexOf('function sourceAccess('),app.indexOf('async function showCompare('));
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function harness(fetchSource=async()=>{throw Error('Source missing');}){
  const nodes=new Map(),requested=[];let focused=null;
  const staticIds=new Set(['sources-content','source-dialog','dialog-title']);
  class Element{
    constructor(id){this.id=id;this.value='';this.listeners={};this.scrollTop=0;this._html='';this.attributes={};}
    set innerHTML(html){
      this._html=html;
      if(this.id==='sources-content')for(const id of nodes.keys())if(!staticIds.has(id))nodes.delete(id);
      for(const [,id] of html.matchAll(/\bid="([^"]+)"/g))nodes.set(id,new Element(id));
      this.card=html.includes('class="source-card')?new Element('card:'+this.id):null;
    }
    get innerHTML(){return this._html;}
    addEventListener(name,listener){this.listeners[name]=listener;}
    setAttribute(name,value){this.attributes[name]=value;}
    focus(){focused=this;}
  }
  for(const id of staticIds)nodes.set(id,new Element(id));
  const select=selector=>{
    if(selector==='#source-dialog .dialog-head h2')return nodes.get('dialog-title');
    if(selector.endsWith(' .source-card'))return nodes.get(selector.split(' ')[0].slice(1))?.card;
    return nodes.get(selector.slice(1));
  };
  const data={meta:{built_at:'fixture-revision',metrics:{posts:3021,post_details:514,years:['2024','2026'],price_kinds:{deal:800,listing:57,reference:27},incremental:{source_manifest:'fixture',social_new_bodies:58}}},
    sources:Array.from({length:1090},(_,index)=>({id:`source:${index}`,label:`来源编号${index}`,notes:'参考价不是备案价',source_as_of:'2026',observed_at:null,url:'https://example.test/'+index,path:'public/evidence.json',sha256:'a'.repeat(64)}))};
  const context={data,sourcePage,$:select,esc:escape,text:escape,num:value=>String(value??'未知'),
    empty:(title,body)=>`<div class="empty">${escape(title)} ${escape(body)}</div>`,href:()=>'',
    openDialog:id=>{nodes.get(id).open=true;},
    get:async url=>{requested.push(url);return fetchSource(url);}};
  vm.runInNewContext(sourceCode+'\nglobalThis.sourceTest={showSources,sourceCoverage};',context);
  return {nodes,requested,data,show:context.sourceTest.showSources,coverage:context.sourceTest.sourceCoverage,
    focused:()=>focused,cards:()=>[...nodes.get(nodes.has('sources-single')?'sources-single':'sources-cards').innerHTML.matchAll(/data-source-card=/g)].length};
}

test('exact source opens one card without constructing the directory',async()=>{
  const h=harness();await h.show('source:1089');
  assert.equal(h.cards(),1);assert.equal(h.nodes.has('sources-search'),false);
  assert.equal(h.requested.length,0);assert.match(h.nodes.get('sources-single').innerHTML,/来源编号1089/);
  assert.match(h.nodes.get('sources-content').innerHTML,/查看来源目录（1090）/);
});
test('directory renders 40 cards; searching and clearing preserve the input node and focus',async()=>{
  const h=harness();await h.show();assert.equal(h.cards(),40);
  const input=h.nodes.get('sources-search');input.value='来源编号1089';input.listeners.input({isComposing:false});
  assert.equal(h.cards(),1);assert.equal(h.nodes.get('sources-search'),input);assert.equal(h.focused(),input);
  h.nodes.get('sources-clear').onclick();assert.equal(h.cards(),40);assert.equal(h.nodes.get('sources-search'),input);assert.equal(input.value,'');
});
test('IME composition waits before updating results and does not replace the input',async()=>{
  const h=harness();await h.show();const input=h.nodes.get('sources-search');
  input.value='来源编号1089';input.listeners.input({isComposing:true});assert.equal(h.cards(),40);
  input.listeners.compositionend();assert.equal(h.cards(),1);assert.equal(h.nodes.get('sources-search'),input);
});
test('paging reaches the final ten sources without accumulating old cards',async()=>{
  const h=harness();await h.show();
  for(let page=1;page<28;page++){h.nodes.get('sources-next').onclick();assert.ok(h.cards()<=40);}
  assert.equal(h.cards(),10);assert.equal(h.nodes.get('sources-next').disabled,true);
  assert.equal(h.nodes.get('sources-page-label').textContent,'28 / 28 页');
  h.nodes.get('sources-prev').onclick();assert.equal(h.cards(),40);
});
test('a source outside the directory is fetched by exact id and remains a single card',async()=>{
  const outside={id:'social:outside',label:'逐条原帖来源',notes:'本地正文观察'};
  const h=harness(async()=>outside);await h.show(outside.id);
  assert.equal(h.cards(),1);assert.equal(h.requested[0],'/api/source?id=social%3Aoutside');
  assert.match(h.nodes.get('sources-single').innerHTML,/逐条原帖来源/);
  await h.nodes.get('sources-show-all').onclick();assert.equal(h.cards(),40);
});
test('missing or mismatched source errors never fall back to all sources',async()=>{
  for(const fetch of [async()=>{throw Error('HTTP 404');},async()=>({id:'wrong',label:'错误来源'})]){
    const h=harness(fetch);await h.show('missing');assert.equal(h.cards(),0);
    assert.match(h.nodes.get('sources-single').innerHTML,/这条来源暂时无法读取/);
    assert.equal(h.nodes.has('sources-search'),false);
  }
});
test('late source responses cannot overwrite a newer source view',async()=>{
  let resolve;const h=harness(()=>new Promise(done=>{resolve=done;}));
  const pending=h.show('outside');await h.show('source:1');
  resolve({id:'outside',label:'迟到来源'});await pending;
  assert.match(h.nodes.get('sources-single').innerHTML,/来源编号1/);
  assert.doesNotMatch(h.nodes.get('sources-single').innerHTML,/迟到来源/);
});
test('closing the dialog prevents a pending request from rendering after dismissal',async()=>{
  let resolve;const h=harness(()=>new Promise(done=>{resolve=done;}));const pending=h.show('outside');
  h.nodes.get('source-dialog').open=false;const loading=h.nodes.get('sources-single').innerHTML;
  resolve({id:'outside',label:'迟到来源'});await pending;
  assert.equal(h.nodes.get('sources-single').innerHTML,loading);
});
test('global coverage keeps time, price, relation and accepted-snapshot limitations',()=>{
  const h=harness(),html=h.coverage();
  for(const note of ['不跨年推断对口','读取日期不当报价日期','不能相加为在售套数','不是对口结论','仅显示已验收快照','不等于校区身份已核验','不是整库时光机'])assert.ok(html.includes(note),note);
});
test('focused single-source card has no redundant top divider',()=>{
  assert.match(css,/\.source-card\.focus-source\{[^}]*border-top:0/);
});
