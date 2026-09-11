import {DEFAULTS,normalize,numeric,located,inside,rectangle,relatedAdmissions,relationPlan,contextHomes,priceMarket,communityProfile,queryEntities,districtTransactionRanking,toggleCompare,readSaved,fundingGap,boundsOf,isFresh,filterPosts,minimumTextSize,policyGroups,sourcePage} from './model.js';
import {enhanceSelects,syncSelects,closeSelectMenu,isSelectMenuOpen} from './controls.js';
import {icon as hi,createIcon,enhanceIcons} from './icons.js';
import {staticDataEnabled,staticGet} from './static-api.js';

const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=0)=>v==null||String(v).trim()===''||!Number.isFinite(Number(v))?'未知':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});
const text=v=>(v===null||v===undefined||v==='')?'未知':esc(v);
const empty=(title,body)=>`<div class="empty"><strong>${esc(title)}</strong>${esc(body)}</div>`;
const icon=e=>`<span class="entity-icon ${e.kind==='residential'?'home':'school'}" aria-hidden="true">${hi(e.kind==='school'?'school':'home',{size:18})}</span>`;
const sourceButton=(id,label='查看来源')=>`<button class="text-button" data-source="${esc(id)}">${esc(label)} ${hi('document')}</button>`;
const href=(url,label)=>/^https?:\/\//.test(url||'')?`<a class="small-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ${hi('external')}</a>`:'';
let data,byId,map,ready=false,allBaseLayers=[],filtered=[],ranked=[],pageSize=40,selected='',detail=null,detailMode='official',searchMode='places';
let state={...DEFAULTS},favorites=[],compare=[],snapshotData=null,detailTicket=0,searchTicket=0,toastTimer,postHits=null,viewStack=[],drawing=false,drawStart=null;
let detailTab='overview',relationQuery='',relationLocatedOnly=false,placesKind=DEFAULTS.kind,priceMarketFilter='all',rankingMetric='count';
let relationCache={key:'',value:null};
const detailCache=new Map();
const savedKey='hangzhou-gis:v1:';
function save(key,value){try{localStorage.setItem(savedKey+key,JSON.stringify(value));return true;}catch{toast('浏览器存储不可用；本次操作只保留在当前页面。');return false;}}
function read(key){try{return localStorage.getItem(savedKey+key);}catch{return null;}}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,3500);}
async function get(url){
  if(staticDataEnabled()&&url.startsWith('/api/'))return staticGet(url);
  if(data&&url.startsWith('/api/')&&!url.startsWith('/api/bootstrap'))url+=(url.includes('?')?'&':'?')+'revision='+encodeURIComponent(data.meta.built_at);
  const r=await fetch(url);
  if(r.status===409){announce('资料库已有新构建，请刷新页面后继续，避免不同版本的数据混用。');throw Error('资料已更新，请刷新地图');}
  if(!r.ok)throw Error(`资料读取失败（${r.status}）`);return r.json();
}
function announce(message){$('#map-message').textContent=message;$('#map-message').hidden=!message;}
function getCandidate(e){return snapshotData?snapshotData[e.id]:e.candidate;}
function officialRelations(id=selected){return relatedAdmissions(id===selected&&detail?{...data,admissions:detail.admissions}:data,id,state);}
function currentRelationPlan(){
  const key=JSON.stringify([selected,detailTicket,!!detail,detailMode,state]);
  if(relationCache.key!==key)relationCache={key,value:relationPlan(data,detail,selected,state,detailMode,{snapshot:snapshotData})};
  return relationCache.value;
}
function shownRelations(plan=currentRelationPlan()){
  const q=normalize(relationQuery);
  return plan.items.filter(r=>byId.has(r.id)&&(!relationLocatedOnly||located(byId.get(r.id)))&&(!q||normalize([byId.get(r.id).name,...(byId.get(r.id).aliases||[]),r.note].join(' ')).includes(q)));
}
function relatedIds(){return selected&&detail?shownRelations().map(r=>r.id):[];}
function syncInputs(){
  for(const [key,value]of Object.entries(state)){const input=document.getElementById(key);if(!input||key==='snapshot')continue;if(input.type==='checkbox')input.checked=!!value;else if(['INPUT','SELECT'].includes(input.tagName))input.value=value??'';}
  $('#search').value=state.query;
  $('#search-clear').hidden=!state.query;
  $$('[data-district]').forEach(b=>b.classList.toggle('active',b.dataset.district===state.district));
  $$('[data-search-mode]').forEach(b=>b.classList.toggle('active',b.dataset.searchMode===searchMode));
  $('#explorer').classList.toggle('ranking-mode',searchMode==='ranking');
  $('#search').placeholder=searchMode==='posts'?`搜索 ${num(data.meta.metrics.posts)} 条本地原帖…`:searchMode==='ranking'?'在成交排行中搜索小区…':'搜索小学、小区、板块…';
  $('#kind').disabled=searchMode==='ranking';
  $('#ranking-controls').hidden=searchMode!=='ranking';
  $('#rankingMetric').value=rankingMetric;
  const scope=byId.get(state.schoolId);
  $('#scope-chip').hidden=!scope;
  $('#scope-chip').innerHTML=scope?`<span>${esc(scope.name)} · ${esc(state.year)} 招生小区</span><button id="clear-school" aria-label="清除学校范围">${hi('close')}</button>`:'';
  $('#clear-rectangle').hidden=!state.rectangle;
  $('#compare-count').textContent=compare.length;
  const count=['candidateOnly','favoriteOnly','officialOnly','builtAfter','minArea','maxArea','minBudget','maxBudget','freshOnly','dealFrom','dealTo'].filter(k=>!!state[k]).length;
  $('#filter-count').textContent=count?`已启用 ${count} 项`:'';
  renderFilterChips();
  syncSelects();
}
function renderFilterChips(){
  const labels={query:'搜索',officialOnly:'官方关系',favoriteOnly:'收藏',candidateOnly:'研究候选',builtAfter:'建成年份 ≥',minArea:'面积 ≥',maxArea:'面积 ≤',minBudget:'总价 ≥',maxBudget:'总价 ≤',freshOnly:'近 30 天',dealFrom:'成交起',dealTo:'成交止',viewportOnly:'当前视野',rectangle:'地图圈选',located:'点位',admission:'招生类型'};
  const enabled=Object.keys(labels).filter(k=>state[k]&&state[k]!==DEFAULTS[k]);
  $('#active-filters').innerHTML=enabled.map(k=>`<button class="active-filter-chip" data-clear-filter="${k}" aria-label="清除${labels[k]}筛选">${esc(labels[k])}${typeof state[k]==='string'?` ${esc(k==='located'?(state[k]==='yes'?'已定位':'待定位'):state[k])}`:''}${hi('close')}</button>`).join('');
}
function change(patch,{fit=false}={}){
  state={...state,...patch};pageSize=40;
  syncInputs();renderQuery();
  renderDetail();
  if(fit)fitResults();
}
function getVisiblePosts(){
  if(!postHits)return [];
  return filterPosts(postHits.posts,data,state,favorites,snapshotData);
}
function renderQuery(){
  if(!data)return;
  renderFilterChips();
  let invalid=false;
  for(const [lo,hi]of [['minBudget','maxBudget'],['minArea','maxArea'],['dealFrom','dealTo']]){
    const present=state[lo]!==''&&state[hi]!=='';
    const reversed=present&&(lo==='dealFrom'?state[lo]>state[hi]:Number(state[lo])>Number(state[hi]));
    for(const key of [lo,hi]){$('#'+key).setAttribute('aria-invalid',String(!!reversed));if(reversed)$('#'+key).setAttribute('aria-describedby','query-feedback');else $('#'+key).removeAttribute('aria-describedby');}
    invalid=invalid||reversed;
  }
  $('#query-feedback').hidden=!invalid;
  $('#query-feedback').textContent=invalid?'筛选下限大于上限，请调整预算、面积或日期范围。':'';
  filtered=queryEntities(data,state,favorites,snapshotData);ranked=[];
  if(searchMode==='ranking'){
    ranked=districtTransactionRanking(data,state,rankingMetric,favorites,snapshotData);
    filtered=ranked.map(row=>row.entity);
    const districtLabel=state.district==='main'?'主城四区':state.district||'八区';
    const metricLabel={count:'成交记录数',unit:'成交单价中位数',total:'可读成交总额',latest:'最近成交日期'}[rankingMetric];
    $('#result-count').textContent=`${districtLabel} · ${ranked.length} 个小区`;
    $('#location-count').textContent=`按${metricLabel}排序 · ${filtered.filter(located).length} 个可落图`;
    $('#ranking-period').textContent=`口径：${state.dealFrom||'最早'} 至 ${state.dealTo||data.meta.metrics.deal_as_of||'最新'}；排除疑似重复。“记录数”含价格未公开记录，不等于登记成交套数。`;
    $('#results').innerHTML=ranked.length?ranked.slice(0,pageSize).map(rankingCard).join(''):empty('当前范围没有已接入成交记录','试试调整行政区、成交日期或其他筛选。没有记录不代表没有成交。');
    if(ranked.length>pageSize)$('#results').insertAdjacentHTML('beforeend',`<button class="load-more" id="load-more">继续显示 · 还有 ${ranked.length-pageSize} 个</button>`);
  }else if(searchMode==='posts'){
    const visiblePosts=getVisiblePosts();
    const ids=new Set(visiblePosts.flatMap(p=>p.place_ids));
    filtered=queryEntities(data,{...state,query:''},favorites,snapshotData).filter(e=>ids.has(e.id));
    $('#result-count').textContent=postHits?`${visiblePosts.length} 条显示原帖`:'正在搜索原帖…';
    $('#location-count').textContent=postHits?`全库匹配 ${postHits.total} 条 · 全量匹配后按空间筛选`:'本地索引，无外部请求';
    $('#results').innerHTML=postHits?(visiblePosts.length?visiblePosts.slice(0,pageSize).map(p=>postCard(p,true)).join(''):empty('当前条件下没有原帖','仅明确提及地点的原帖参与空间定位；未定位原帖不伪造坐标。')):'';
    if(visiblePosts.length>pageSize)$('#results').insertAdjacentHTML('beforeend','<button class="load-more" id="load-more">继续显示</button>');
  }else{
    const n=filtered.filter(located).length;
    $('#result-count').textContent=`${filtered.length.toLocaleString()} 个${state.kind==='school'?'学校 / 校区':state.kind==='residential'?(state.schoolId?'小区 / 地址':'小区及住宅线索'):'地点'}`;
    $('#location-count').textContent=`${n.toLocaleString()} 个点位 · ${filtered.length-n} 个待定位`;
    $('#results').innerHTML=filtered.length?filtered.slice(0,pageSize).map(resultCard).join(''):empty('没有符合条件的地点',state.year==='2029'&&state.officialOnly?'本地资料没有 2029 官方招生名单。不会套用 2026 年记录。':'试试清除圈选、学校范围或预算条件。缺失资料默认不满足数值筛选。');
    if(filtered.length>pageSize)$('#results').insertAdjacentHTML('beforeend',`<button class="load-more" id="load-more">继续显示 · 还有 ${filtered.length-pageSize} 个</button>`);
  }
  if(state.year==='2029'&&(state.officialOnly||state.schoolId))announce('没有 2029 年官方招生名单；当前年度结果为空，不使用往年记录回填。');
  else if(['滨江区','拱墅区'].includes(state.district)&&state.officialOnly)announce(`${state.district}已接入官方目录；当前仅显示 ${state.year} 年有直接名单关系的对象。`);
  else if(drawing)announce(drawStart?'再点击地图一个角，完成矩形圈选。':'点击地图两个对角，筛选矩形内的地点。');
  else if(!map?.hasGISNetworkError)announce('');
  renderMap();
}
function rankingCard(row){
  const primary=row.metric==='unit'?(row.medianUnit?`${num(row.medianUnit)} 元/㎡`:'单价待补'):row.metric==='total'?(row.totalWan?`${num(row.totalWan,1)} 万`:'总额待补'):row.metric==='latest'?(row.latestDate||'日期待补'):`${num(row.dealCount)} 条`;
  return `<button class="ranking-card ${row.entity.id===selected?'selected':''}" data-select="${esc(row.entity.id)}" aria-label="第 ${row.rank} 名，${esc(row.entity.name)}，${esc(primary)}"><span class="ranking-position">${row.rank}</span><span class="ranking-main"><strong>${esc(row.entity.name)}</strong><small>${esc(row.entity.district||'区属待核')} · ${row.dealCount} 条记录 · ${row.pricedCount}/${row.dealCount} 条单价可读</small><span>${row.medianUnit?`单价中位数 ${num(row.medianUnit)} 元/㎡`:'成交单价待补'} · ${row.latestDate?`最近 ${esc(row.latestDate)}`:'日期待补'}</span></span><span class="ranking-value"><strong>${primary}</strong>${hi('arrow-right')}</span></button>`;
}
function resultCard(e){
  const c=getCandidate(e);const annual=e.official_years?.includes(state.year);
  const affiliation=(data.school_campus_links||[]).some(l=>l.campus_id===e.id&&l.year===state.year&&l.kind==='documented_campus_affiliation');
  return `<button class="result-card ${e.id===selected?'selected':''}" data-select="${esc(e.id)}" aria-pressed="${e.id===selected}">${icon(e)}<span class="entity-info"><span class="entity-name">${esc(c?.name||e.name)}</span><span class="entity-meta">${esc(e.district||'行政区待核')} · ${c?.plate?esc(c.plate):e.kind==='school'?'小学 / 校区':'住宅小区'}${e.post_count?` · ${e.post_count} 条原帖`:''}</span><span class="result-stats">${annual?`<span class="tag ${affiliation?'amber':''}">${affiliation?'校区沿革参考':esc(state.year)+' 官方档案'}</span>`:''}${c?`<span class="tag gray">候选 #${text(c.rank)}</span>`:''}${!located(e)?'<span class="tag amber">待定位</span>':''}${favorites.includes(e.id)?'<span class="tag">已收藏</span>':''}</span></span><span class="entity-arrow" aria-hidden="true">${hi('chevron-right')}</span></button>`;
}
function postCard(p,links=false){
  return `<article class="post-card"><div class="post-meta">${p.depth==='detail_description'?'正文 / 说明':'仅索引，未深读'} · ${text(p.posted_date?.slice(0,10))}</div><h3>${esc(p.title||'无标题')}</h3>${p.description?`<p>${esc(p.description)}</p>`:''}${links?`<div class="place-links">${p.place_ids.filter(id=>byId.has(id)).map(id=>`<button data-select="${esc(id)}">${esc(byId.get(id).name)} ${hi('arrow-right')}</button>`).join('')}</div>${!p.place_ids.length?'<span class="tag amber">未提取可靠地点，不落图</span>':''}`:''}${href(p.url,'查看原帖')}</article>`;
}
async function searchPosts(){
  const ticket=++searchTicket;postHits=null;renderQuery();
  try{const result=await get('/api/posts?q='+encodeURIComponent(state.query));if(ticket!==searchTicket)return;postHits=result;renderQuery();}
  catch(e){if(ticket===searchTicket){$('#results').innerHTML=empty('原帖读取失败',e.message);toast(e.message);}}
}
function currentView(){return {selected,state:{...state},searchMode,detailMode,detailTab,priceMarketFilter,rankingMetric,relationQuery,relationLocatedOnly,detailScroll:$('.detail-body')?.scrollTop||0,listScroll:$('#results').scrollTop,center:map?.getCenter().toArray(),zoom:map?.getZoom()};}
function updateMapInsets(){
  if(!map)return;
  const stage=$('.map-stage').getBoundingClientRect(),panel=$('#detail').getBoundingClientRect();
  if(Math.abs(map.getCanvas().clientWidth-stage.width)>1||Math.abs(map.getCanvas().clientHeight-stage.height)>1)map.resize();
  const showing=!$('#detail').hidden;
  const padding={top:60,left:20,right:20,bottom:110};
  if(innerWidth<=1100&&innerWidth>760&&showing)padding.right=Math.min(stage.width-100,panel.width+30);
  if(innerWidth<=760&&showing)padding.bottom=Math.min(stage.height*.7,panel.height+98);
  const old=map.getPadding();
  if(Object.keys(padding).some(k=>Math.abs(old[k]-padding[k])>1))map.setPadding(padding);
}
async function selectEntity(id,{push=true,focus=true}={}){
  if(!byId.has(id))return;
  if(push&&selected&&selected!==id)viewStack.push(currentView());
  selected=id;detail=null;detailMode='official';detailTab='overview';relationQuery='';relationLocatedOnly=false;priceMarketFilter='all';const ticket=++detailTicket;
  history.replaceState(null,'',`#place=${encodeURIComponent(id)}`);
  $('#detail').hidden=false;$('#explorer').classList.remove('mobile-open');
  document.body.classList.remove('detail-collapsed','explorer-open');
  $('#detail').scrollTop=0;
  $('#detail').innerHTML='<div class="empty">正在连接地点与证据…</div>';
  map?.resize();updateMapInsets();renderQuery();
  try{const result=detailCache.get(id)||await get('/api/entity?id='+encodeURIComponent(id));detailCache.set(id,result);if(ticket!==detailTicket)return;detail=result;renderDetail();renderMap();
    if(focus&&located(byId.get(id))&&map)map.easeTo({center:[byId.get(id).lng,byId.get(id).lat],zoom:Math.max(map.getZoom(),14.2),duration:450});
  }
  catch(e){if(ticket===detailTicket)$('#detail').innerHTML=empty('详情未能读取',e.message)+'<button id="close-detail" class="load-more">关闭面板</button>';}
}
function closeDetail(){selected='';detail=null;detailTicket++;viewStack=[];$('#detail').hidden=true;document.body.classList.remove('detail-collapsed');history.replaceState(null,'',location.pathname+location.search);map?.resize();updateMapInsets();renderQuery();}
async function back(){
  const old=viewStack.pop();if(!old)return closeDetail();
  const snapshotChanged=old.state.snapshot!==state.snapshot;
  state=old.state;searchMode=old.searchMode;syncInputs();
  if(snapshotChanged)await setSnapshot(data.meta.metrics.snapshot_dates.indexOf(old.state.snapshot));
  await selectEntity(old.selected,{push:false,focus:false});detailMode=old.detailMode;detailTab=old.detailTab;priceMarketFilter=old.priceMarketFilter||'all';rankingMetric=old.rankingMetric||'count';relationQuery=old.relationQuery;relationLocatedOnly=old.relationLocatedOnly;renderDetail();renderQuery();
  if($('.detail-body'))$('.detail-body').scrollTop=old.detailScroll;$('#results').scrollTop=old.listScroll;
  if(old.center)map?.jumpTo({center:old.center,zoom:old.zoom});
}
const tierLabels={"official":"年度名单关系","affiliation":"校区沿革参考","co":"同帖提及","nearby":"附近参考","district":"同区核验入口","none":"待补充核验"};
const tierNotes={"official":"名单按年度与招生类型筛选；地图名称匹配仍需核对地址、分期和楼栋。","affiliation":"依据校区沿革展示所属学校的名单参考，不确认该物理校区当年对口。","co":"仅表示原帖共同提及，不是招生关系。","nearby":"2 公里内对象中心的直线距离，不代表学区或步行距离。","district":"当前地点尚未完成直接挂接；以下为同区核验入口，不是对口结论。","none":"当前条件尚无可用核验线索；可调整筛选或查看来源，不能据此判断不对口。"};
function relationListHTML(plan=currentRelationPlan()){
  const items=shownRelations(plan);
  return items.length?items.map(r=>{const e=byId.get(r.id);return `<button class="relation-item" data-select="${esc(r.id)}"><span>${esc(e.name)}${!located(e)?'<span class="tag amber">待定位</span>':''}<small>${esc(r.note)}${r.rows>1?` · ${r.rows} 条明细`:''}</small></span>${hi('arrow-right')}</button>`;}).join(''):empty('没有匹配的关联对象',relationQuery||relationLocatedOnly?'清除关联搜索或关闭“仅已定位”即可查看其他记录。':tierNotes.none);
}
function updateRelationList(){
  if(!$('#relation-list'))return;
  const plan=currentRelationPlan(),items=shownRelations(plan),n=items.filter(r=>located(byId.get(r.id))).length;
  $('#relation-list').innerHTML=relationListHTML(plan);
  $('#relation-counts').textContent=`显示 ${items.length} / ${plan.items.length} 个 · ${n} 已定位 · ${items.length-n} 待定位`;
  $('#fit-relations').disabled=!n;
  renderMap();
}
function renderRelations(){
  const plan=currentRelationPlan(),school=byId.get(selected)?.kind==='school';
  return `<section class="section relation-section"><div class="section-header"><h3>${school?'关联小区 / 招生地址':'关联学校 / 校区'}</h3><small>${plan.items.length} 个对象</small></div>
    <div class="relation-tabs" role="group" aria-label="关系证据类型">${[['official','年度名单'],['co','同帖提及'],['nearby','附近参考']].map(([mode,label])=>`<button data-relation="${mode}" class="${detailMode===mode?'active':''}" aria-pressed="${detailMode===mode}">${label}</button>`).join('')}</div>
    <div class="relationship-summary" data-tier="${plan.tier}"><strong>${plan.tier==='official'?esc(state.year)+' · ':''}${tierLabels[plan.tier]}</strong><p>${esc(tierNotes[plan.tier])}</p>${plan.fallback&&plan.tier!=='none'?`<span class="detail-compact-note">当前展示${tierLabels[plan.tier]}${plan.tier==='affiliation'?'，非独立招生确认':'，非当前标签直接关系'}</span>`:''}</div>
    <div class="relation-tools"><button id="fit-relations">地图看全关联</button><button id="show-relations" class="${state.markers==='relations'?'active':''}" aria-pressed="${state.markers==='relations'}">${state.markers==='relations'?'恢复结果点位':'只看这些关联'}</button></div>
    ${plan.canScope?`<button class="primary" id="scope-school">用此学校筛选小区 ${hi('arrow-right')}</button>`:''}
    <div class="relation-search"><input id="relation-search" type="search" value="${esc(relationQuery)}" placeholder="${school?'在关联小区 / 地址中搜索':'在关联学校中搜索'}" aria-label="搜索当前关联对象"></div>
    <div class="relation-counts"><span id="relation-counts"></span><label><input id="relation-located-only" type="checkbox" ${relationLocatedOnly?'checked':''}>仅已定位</label></div>
    <div id="relation-list" class="relation-list">${relationListHTML(plan)}</div>
  </section>`;
}
function priceCard(p){
  const raw=p.payload||{},date=p.kind==='deal'?p.event_date:(raw.price_as_of||raw.source_as_of||p.event_date),market=priceMarket(p);
  const enrichments=Array.isArray(raw.price_enrichments)?raw.price_enrichments:[];
  const enrichmentEvidence=enrichments.map(e=>`<div class="micro"><span class="tag">公开详情补价 · 非新增交易</span><p>补充读取 ${esc(e.observed_at||'未知')}；原记录与来源保留，不代表官方登记核验。</p>${href(e.evidence?.source_url,'查看补价详情')}${e.source_id?sourceButton(e.source_id,'补价来源'):''}</div>`).join('');
  const total=numeric(p.total_wan),unit=numeric(p.unit_yuan_sqm),area=numeric(p.area_sqm),hasTotal=total!==null&&total>0,hasUnit=unit!==null&&unit>0;
  const duplicate=!!raw.possible_duplicate_group||Number(raw.baseline_possible_business_matches)>0;
  const dateLabel=p.kind==='deal'?'成交日期':p.kind==='reference'?'参考统计期':'报价日期';
  const preciseDate=date&&/^\d{4}-\d{2}-\d{2}(?:T|$)/.test(date),recent=preciseDate&&isFresh(String(date).slice(0,10));
  const timeNote=!date?`${dateLabel}未知`:!preciseDate?'统计期精度不足，时效待核':recent?'近 30 天记录':'非近 30 天';
  const marketLabel=market==='new'?'新房历史参考':market==='resale'?'二手房':'类型待核';
  const headline=p.kind==='reference'&&hasUnit?`${num(unit)} 元/㎡`:hasTotal?`${num(total,1)} 万`:hasUnit?`${num(unit)} 元/㎡`:
    p.kind==='deal'?'有成交记录 · 价格未公开':p.kind==='listing'?'有挂牌线索 · 价格未公开':'有参考记录 · 价格未公开';
  const kindLabel=p.kind==='deal'?'历史成交样本':p.kind==='listing'?'挂牌线索 · 未核在售':'参考记录 · 非成交价';
  return `<div class="price-item"><div class="price-title"><strong>${headline}</strong><small>${date?`${dateLabel} ${esc(date)}`:`${dateLabel}未知`}</small></div><p>${p.kind==='reference'?'参考价口径，不是单套房源报价':`${area!==null&&area>0?`${num(area,2)} ㎡`:'面积未知'} · ${hasUnit?`${num(unit)} 元/㎡`:'单价未公开'}${!hasTotal&&hasUnit?' · 总价未公开':''}${raw.layout?` · ${esc(raw.layout)}`:''}${raw.orientation?` · ${esc(raw.orientation)}`:''}`}</p><span class="tag ${recent?'':'amber'}">${marketLabel} · ${kindLabel} · ${timeNote}</span>${duplicate?'<span class="tag amber">疑似重复 · 未确认独立房屋</span>':''}${raw.price_disclosure==='masked_or_missing'?`<p class="micro">${enrichments.length?'原列表价格曾打码或缺失；现由独立公开详情补充，未通过计算还原打码价格。':'来源价格打码或缺失，保留成交记录，不补算成交价。'}</p>`:''}${raw.source_type==='search_snippet'?'<span class="tag amber">仅搜索摘要</span>':''}${raw.note?`<p>${esc(raw.note)}</p>`:''}${p.observed_at?`<p class="micro">读取 / 观察 ${esc(p.observed_at)}；不代表报价或成交发生时间。</p>`:''}${href(raw.url,'公开价格线索')}${sourceButton(p.source_id)}${enrichmentEvidence}</div>`;
}
function marketSummarySection(e){
  const prices=detail.prices||[],quoteDate=p=>p.kind==='deal'?p.event_date:(p.payload?.price_as_of||p.payload?.source_as_of||p.event_date);
  const sorted=(rows,observations=false)=>[...rows].sort((a,b)=>String(observations?b.observed_at||'':quoteDate(b)||'').localeCompare(String(observations?a.observed_at||'':quoteDate(a)||'')));
  const listings=sorted(prices.filter(p=>p.kind==='listing'),true),deals=sorted(prices.filter(p=>p.kind==='deal'&&(!state.dealFrom||p.event_date>=state.dealFrom)&&(!state.dealTo||p.event_date<=state.dealTo)));
  const duplicate=p=>!!p.payload?.possible_duplicate_group||Number(p.payload?.baseline_possible_business_matches)>0,hasPrice=p=>(numeric(p.total_wan)||0)>0||(numeric(p.unit_yuan_sqm)||0)>0;
  const reference=sorted(prices.filter(p=>p.kind==='reference')),priced=deals.filter(hasPrice),missing=deals.length-priced.length,duplicates=deals.filter(duplicate).length;
  const units=deals.filter(p=>!duplicate(p)).map(p=>numeric(p.unit_yuan_sqm)).filter(v=>v!==null&&v>0),latestDeal=deals[0]?.event_date,c=getCandidate(e);
  const metric=(p,label)=>{
    const total=numeric(p?.total_wan),unit=numeric(p?.unit_yuan_sqm),area=numeric(p?.area_sqm),date=p&&quoteDate(p);
    const headline=!p?'暂无已接入记录':total!==null&&total>0?`${num(total,1)}<small> 万</small>`:unit!==null&&unit>0?`${num(unit)}<small> 元/㎡</small>`:'有记录 · 价格未公开';
    return `<div class="market-metric"><label>${label}</label><strong>${headline}</strong><small>${p?`${unit!==null&&unit>0?`${num(unit)} 元/㎡`:'单价未公开'} · ${area!==null&&area>0?`${num(area,1)} ㎡`:'面积未知'}${duplicate(p)?' · 疑似重复':''}`:'未提供不等于没有房源'}</small><small class="market-date">${p?`${p.kind==='deal'?'成交':'报价'}日期 ${esc(date||'未知')}${p.observed_at?` · 读取 ${esc(String(p.observed_at).slice(0,10))}`:''}`:''}</small></div>`;
  };
  const newCount=prices.filter(p=>priceMarket(p)==='new').length,resaleCount=prices.filter(p=>priceMarket(p)==='resale').length;
  return `<section class="section market-summary"><div class="section-header"><h3>价格与行情</h3><small>${latestDeal?'最近成交日期 '+esc(latestDeal):prices.length?`${prices.length} 条相关记录`:'记录待补充'}</small></div><div class="market-metrics">${metric(listings[0],listings[0]&&priceMarket(listings[0])==='resale'?'最近读取二手挂牌线索':'最近读取挂牌线索')}${metric(deals[0],deals[0]&&priceMarket(deals[0])==='resale'?'最近二手成交记录':'最近成交记录')}</div>
    <p class="market-status">${deals.length?`${deals.length} 条成交记录 · ${priced.length} 条价格可读 · ${missing} 条价格未公开。`:'当前日期范围暂无已接入成交记录。'}${units.length?` ${units.length} 条有效单价 ${num(Math.min(...units))}—${num(Math.max(...units))} 元/㎡${duplicates?'（排除疑似重复记录）':''}。`:' 缺少可用于单价比较的成交样本。'}${duplicates?` ${duplicates} 条疑似重复保留待核，不据此统计独立成交套数。`:''} 不据少量样本推算涨跌。${!deals.length&&(numeric(reference[0]?.unit_yuan_sqm)||0)>0?` 参考记录 ${num(reference[0].unit_yuan_sqm)} 元/㎡（统计期 ${esc(quoteDate(reference[0])||'未知')}，非成交价）。`:''}${!prices.length&&c?.unit_price?` 候选研究单价 ${num(c.unit_price)} 元/㎡（${esc(state.snapshot)} 观察快照，非成交价）。`:''}</p>
    <p class="market-status">二手房 ${resaleCount} 条已识别记录 · 新房 ${newCount?`${newCount} 条历史参考`:'暂无已接入记录'}。${prices.some(p=>priceMarket(p)==='unknown')?'另有未区分类型的价格线索。':''}</p>
    <button class="market-open" data-detail-tab="housing">查看新房 / 二手房与价格明细 ${hi('arrow-right')}</button>
  </section>`;
}
function priceSection(){
  const all=detail.prices||[],records=all.filter(p=>priceMarketFilter==='all'||priceMarket(p)===priceMarketFilter);
  const groups=[['listing','单套挂牌线索'],['deal','历史成交样本'],['reference','参考价记录']];
  return `<section class="section"><div class="section-header"><h3>新房 / 二手房行情</h3><small>按来源口径区分</small></div>
    <div class="relation-tabs" role="group" aria-label="价格市场类型">${[['all','全部'],['resale','二手房'],['new','新房'],['unknown','未区分']].map(([id,label])=>`<button data-price-market="${id}" class="${priceMarketFilter===id?'active':''}" aria-pressed="${priceMarketFilter===id}">${label}</button>`).join('')}</div>
    <p class="micro">挂牌不等于在售核验，成交样本不是实时网签；新房参考不冒充新房成交。每条保留日期和来源。</p>
    ${!records.length?empty('此口径暂无已接入价格记录','可查看其他口径或候选观察。缺失记录不代表没有成交或房源。'):groups.map(([kind,label])=>{
      const raw=records.filter(p=>p.kind===kind);
      const dates=kind==='deal'?raw.filter(p=>(!state.dealFrom||p.event_date>=state.dealFrom)&&(!state.dealTo||p.event_date<=state.dealTo)):raw;
      return `<details ${dates.length?'open':''}><summary>${label} · ${dates.length} 条${kind==='deal'&&(state.dealFrom||state.dealTo)?'（选定日期）':''}</summary>${dates.length?dates.sort((a,b)=>(b.event_date||b.observed_at||'').localeCompare(a.event_date||a.observed_at||'')).map(priceCard).join(''):'<p class="micro">没有匹配记录，不代表没有成交或在售。</p>'}</details>`;
    }).join('')}
    <p class="micro">成交来源截至 ${esc(data.meta.metrics.deal_as_of)}。先核对小区、房屋与日期，再作判断；单日或少量样本不推算市场涨跌。</p></section>`;
}
function candidateSection(e){
  const c=getCandidate(e);if(!c)return `<section class="section"><h3>研究候选</h3><p class="micro">${snapshotData?'该候选观察快照':'当前候选池'}中没有此地点记录。</p></section>`;
  const pairs=[['候选排名',c.rank],['板块',c.plate],['价格口径',c.price_source],['估算总价',c.total_range],['建成年份',c.built_year],['年代范围',c.age_band],['最近地铁',c.nearest_metro],['地铁参考',c.metro_text],['江边参考',c.river_text],['学校线索',c.school],['风险',c.risk],['候选理由',c.reason],['覆盖说明',c.source_note]];
  return `<section class="section"><div class="section-header"><h3>候选观察档案</h3><small>${esc(state.snapshot)}</small></div><div class="metric-grid"><div class="metric-cell"><strong>${num(c.unit_price)}</strong><label>研究记录单价 · 元/㎡，非今日报价</label></div><div class="metric-cell"><strong>#${num(c.rank)}</strong><label>候选排序 · 不是学校排名</label></div></div><dl class="kv">${pairs.filter(([,v])=>v!==null&&v!==undefined&&v!=='').map(([k,v])=>`<dt>${esc(k)}</dt><dd>${text(v)}</dd>`).join('')}</dl>${sourceButton(state.snapshot===data.meta.metrics.candidate_snapshot?'candidates':'candidate-history:'+state.snapshot,'此观察快照来源')}<p class="micro">估算、学校研究线索与交通文字保留原口径；学校归属以年度官方名单和楼栋核验为准。</p>${historyChart(detail.history)}</section>`;
}
function historyChart(history){
  const points=history.filter(r=>r.snapshot_date<=state.snapshot&&numeric(r.payload.unit_price)!==null);
  if(points.length<8)return `<p class="micro">有效单价观察 ${points.length} 个时点，不足以绘制有意义的走势；请使用底部快照切换逐期查看。</p>`;
  const values=points.map(r=>Number(r.payload.unit_price)),lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1;
  const start=Date.parse(points[0].snapshot_date),end=Date.parse(points.at(-1).snapshot_date),duration=end-start||1;
  const coords=values.map((v,i)=>`${15+(Date.parse(points[i].snapshot_date)-start)/duration*270},${70-(v-lo)/span*50}`).join(' ');
  return `<svg class="sparkline" viewBox="0 0 300 95" role="img" aria-label="${esc(points[0].snapshot_date)} 至 ${esc(points.at(-1).snapshot_date)}，${points.length} 次候选观察记录单价，非成交走势图"><path d="M15 73H285" stroke="#d9e3cc"/><polyline points="${coords}" fill="none" stroke="#78976a" stroke-width="1.7"/><text x="15" y="88" fill="#8b9d7c" font-size="12">${esc(points[0].snapshot_date)}</text><text x="285" y="88" text-anchor="end" fill="#8b9d7c" font-size="12">${esc(points.at(-1).snapshot_date)}</text><text x="15" y="13" fill="#69845a" font-size="12">候选观察记录单价 ${num(lo)}—${num(hi)} 元/㎡</text></svg><p class="history-caption">${points.length} 个日快照。不变的记录可能只是复用旧数据，不能解读为实际房价稳定。</p>`;
}
function policyDocument(p,e){
  const archive=p.kind==='local_policy_archive';
  const places=archive?(p.school_ids||[]).filter(id=>id!==e.id&&byId.has(id)):[];
  return `<details><summary>${esc(p.label)}</summary>${archive?'<p class="micro">本地保存的官方页面副本，非本轮原站复核。按文中年度、范围与自愿等条件对照，不生成确定对口连线。</p>':''}<div class="policy">${esc(p.body)}</div>${places.length?`<p class="micro">本档案涉及的其他地点；同一文档不代表彼此对口：</p><div class="place-links">${places.map(id=>`<button data-select="${esc(id)}">${esc(byId.get(id).name)} ${hi('arrow-right')}</button>`).join('')}</div>`:''}${sourceButton(p.source_id,'文本来源')}</details>`;
}
function renderEvidence(e){
  const links=(detail.school_links||[]).filter(l=>l.year===state.year&&(l.campus_id===e.id||l.official_school_id===e.id));
  const linkedIds=new Set(links.map(l=>l.official_school_id));
  const annual=detail.school_records.filter(r=>r.year===state.year&&(r.entity_id===e.id||linkedIds.has(r.entity_id)));
  const {current:policy,otherArchives}=policyGroups(detail.policies,state.year);
  return `<section class="section"><h3>地点身份与依据</h3><dl class="kv"><dt>地点 ID</dt><dd>${esc(e.id)}</dd><dt>点位口径</dt><dd>${located(e)?(e.location_status==='official_portal_gcj02_to_wgs84'?'官方门户坐标转换；非校门入口':'OSM 对象中心；非校门或房屋入口'):'尚无可靠坐标，未落点'}</dd><dt>匹配状态</dt><dd>${links.some(l=>l.kind==='documented_campus_affiliation')?'有历史校区归属证据，当年物理校区招生仍待核验':links.length?'官方编号与地图点位近邻匹配，名称 / 地址待复核':'名称关联待核对，校区与楼栋不自动合并'}</dd></dl>${href(e.osm_url,'OSM 地理对象')}${links.map(l=>`<details><summary>${l.kind==='documented_campus_affiliation'?'校区沿革依据':'点位匹配依据'} · ${esc(l.year)}</summary><p class="micro">${esc(l.payload?.evidence||`同区坐标近邻 ${l.distance_m??'未知'} 米；距离不是校区身份核验。`)}</p>${sourceButton(l.source_id)}</details>`).join('')}</section>
  ${annual.length?`<section class="section"><h3>${e.kind==='school'?'相关年度学校档案':'年度学校资料'} · ${esc(state.year)}</h3>${annual.map(r=>`<details><summary>${esc(r.payload.school_name||r.payload.show_school_name||r.official_id)}</summary><dl class="kv"><dt>官方编号</dt><dd>${esc(r.official_id)}</dd><dt>档案地址</dt><dd>${esc(r.payload.address||'未提供')}</dd><dt>咨询电话</dt><dd>${esc(r.payload.school_tel||'未提供')}</dd></dl><p class="micro">档案地址属于上述学校档案，不自动替换当前选中校区地址。</p>${sourceButton(r.source_id)}</details>`).join('')}</section>`:''}
  ${policy.length?`<section class="section"><h3>当年政策与服务区文本</h3>${policy.map(p=>policyDocument(p,e)).join('')}<p class="micro">道路边界未转换为可核验几何，不绘制推测学区。</p></section>`:''}
  ${otherArchives.length?`<section class="section"><h3>其他年度政策档案</h3><p class="micro">以下不是当前 ${esc(state.year)} 年招生依据。历史年度与转引、自愿报名等条件均按原文保留。</p>${otherArchives.map(p=>policyDocument(p,e)).join('')}</section>`:''}
  <section class="section"><h3>来源匹配记录</h3><details><summary>${detail.mappings.length} 条来源匹配</summary>${detail.mappings.map(m=>`<p class="source-line">${esc(m.source_id)} · ${esc(m.source_record)}<br>${esc(({name_match_review_required:'唯一名称匹配 · 地址待核',unmatched:'未找到地图名称',ambiguous:'名称有歧义',same_name_unlocated:'同名未定位实体'})[m.status]||m.status)}</p>`).join('')}</details>${sourceButton(e.district==='拱墅区'?'education-directory:330105':e.district==='滨江区'?'education-directory:330108':'admissions','查看资料覆盖与来源')}</section>`;
}
function renderDetail(){
  if(!selected||!detail)return;
  const e=byId.get(selected),school=e.kind==='school';
  const ownRecord=detail.school_records.find(r=>r.year===state.year&&r.entity_id===selected);
  const residentialAddress=!school?communityProfile(e,detail).fields.find(item=>item.label==='楼盘位置')?.value:null;
  // A linked institution's address must never overwrite the selected physical campus.
  const address=e.address||residentialAddress||ownRecord?.payload.address||(school?'当前校区详细地址待核实':'详细地址待核实');
  const oldScroll=$('.detail-body')?.scrollTop||0,focusId=$('#detail').contains(document.activeElement)?document.activeElement.id:'';
  const tabs=[['overview',school?'关联':'概览'],...(!school?[['housing','价格行情']]:[]),...((detail.projects||[]).length?[['projects','楼盘档案']]:[]),['posts',`原帖 ${detail.posts.length}`],['sources','依据']];
  if(!tabs.some(([id])=>id===detailTab))detailTab='overview';
  const outside=!filtered.some(x=>x.id===selected);
  const content=detailTab==='overview'?`${school?'':communityProfileSection(e)}${school?'':marketSummarySection(e)}${(detail.projects||[]).length?`<button class="load-more" data-detail-tab="projects">${hi('home')} 楼盘档案 · 开发商、销售状态与许可证</button>`:''}${outside?'<p class="detail-compact-note">选中地点独立保留，不受左侧筛选隐藏。</p>':''}${!located(e)?'<div class="notice warning">当前地点待定位；关联资料仍可查看，有坐标的对象可单独落图。</div>':''}${renderRelations()}`:
    detailTab==='housing'?priceSection()+marketSnapshotsSection()+candidateSection(e):
    detailTab==='projects'?projectSection():
    detailTab==='posts'?`<section class="section"><div class="section-header"><h3>原帖与观察</h3><small>${detail.posts.length} 条</small></div>${detail.posts.length?detail.posts.map(p=>postCard(p)).join(''):empty('未提取到明确提及该地点的原帖','可在左侧切换“原帖正文 / 标题”，检索本地全库。')}${sourceButton('posts')}</section>`:renderEvidence(e);
  $('#detail').innerHTML=`<div class="detail-header"><div class="detail-nav"><button class="back" id="back-detail">${hi('arrow-left')} ${viewStack.length?'上一地点':'返回地图'}</button><span>${esc(e.district||'区属待核')}</span><button id="inspector-collapse" class="inspector-collapse" aria-expanded="${!document.body.classList.contains('detail-collapsed')}" aria-label="展开或收起地点详情">${hi(document.body.classList.contains('detail-collapsed')?'chevron-up':'chevron-down')}${document.body.classList.contains('detail-collapsed')?'展开':'收起'}</button><button id="close-detail" aria-label="关闭地点详情">${hi('close')}</button></div><div class="detail-identity"><span class="eyebrow">${school?'学校 / 校区':'住宅 / 小区'}</span><h2>${esc(e.name)}</h2><div class="address">${esc(address)}${!located(e)?' · 待定位':''}</div></div><div class="detail-actions"><button id="locate-entity" ${located(e)?'':'disabled'}>${hi('locate')} 地图定位</button><button id="favorite-entity" class="${favorites.includes(e.id)?'active':''}" aria-pressed="${favorites.includes(e.id)}">${hi(favorites.includes(e.id)?'check':'star')} ${favorites.includes(e.id)?'已收藏':'收藏'}</button>${!school?`<button id="compare-entity" class="${compare.includes(e.id)?'active':''}" aria-pressed="${compare.includes(e.id)}">${hi('compare')} ${compare.includes(e.id)?'移出比较':'比较'}</button>`:''}</div></div>
  <div class="detail-tabs" role="tablist" aria-label="地点资料">${tabs.map(([id,label])=>`<button id="detail-tab-${id}" role="tab" aria-selected="${detailTab===id}" aria-controls="detail-panel" tabindex="${detailTab===id?0:-1}" data-detail-tab="${id}" class="${detailTab===id?'active':''}">${label}</button>`).join('')}</div>
  <div class="detail-body"><div id="detail-panel" class="detail-panel" role="tabpanel" aria-labelledby="detail-tab-${detailTab}" data-detail-panel="${detailTab}">${content}</div></div>`;
  $('.detail-body').scrollTop=oldScroll;
  if(focusId)document.getElementById(focusId)?.focus({preventScroll:true});
  if(detailTab==='overview')updateRelationList();
  updateMapInsets();
}

function communityProfileSection(e){
  const profile=communityProfile(e,detail);
  const priceDate=profile.price.asOf?`统计 / 事件期 ${esc(profile.price.asOf)}`:profile.price.observedAt?`读取 ${esc(profile.price.observedAt)} · 统计期未公开`:'日期待补充';
  const priceValue=profile.price.value?`${num(profile.price.value)}<small> 元/㎡</small>`:'待补充';
  const timingValue=profile.timing.value?esc(profile.timing.value):'待补充';
  const rows=profile.fields.map(item=>`<div class="profile-row${item.value?'':' is-missing'}"><dt>${esc(item.label)}</dt><dd>${item.value?esc(item.value):'待补充'}</dd></div>`).join('');
  const surroundings=profile.surrounding.length?`<details class="profile-surroundings"><summary>周边与生活配套 ${hi('chevron-down')}</summary><dl>${profile.surrounding.map(item=>`<div class="profile-row"><dt>${esc(item.label)}</dt><dd>${esc(item.value)}</dd></div>`).join('')}</dl><p class="micro">配套文字来自楼盘资料页，只作位置核验线索；教育关系仍以下方年度名单、同帖与附近分层结果为准。</p></details>`:'';
  return `<section class="section community-profile" data-testid="community-profile"><div class="section-header"><h3>小区关键信息</h3><small class="profile-completeness${profile.passesCompleteness?' is-complete':''}">已收录 ${profile.known} / ${profile.total} 项 · 待补 ${profile.missingPercent}%${profile.passesCompleteness?' · 已达标':''}</small></div>
    <div class="profile-highlights"><article><span>${esc(profile.timing.label)}</span><strong>${timingValue}</strong><small>${esc(profile.timing.basis)}</small></article><article><span>${esc(profile.price.label)}</span><strong>${priceValue}</strong><small>${esc(profile.price.basis)}</small><small>${esc(priceDate)}${profile.price.count>1?` · ${num(profile.price.count)} 条样本`:''}</small></article></div>
    <dl class="profile-list">${rows}</dl>${surroundings}
    <div class="profile-evidence"><span>${profile.observedAt?`楼盘资料读取 ${esc(profile.observedAt)}`:'楼盘资料日期待补充'}；均价按可用来源优先级展示，不混合新房参考价、挂牌与历史成交。</span>${profile.sourceId?sourceButton(profile.sourceId,'查看楼盘资料来源'):''}${profile.price.sourceId&&profile.price.sourceId!==profile.sourceId?sourceButton(profile.price.sourceId,'查看均价来源'):''}</div></section>`;
}

function projectSection(){
  const priceNames={platform_reference:'平台参考价',platform_algorithm_reference:'平台算法参考价'};
  return (detail.projects||[]).map(row=>{
    const p=row.payload||{};
    const prices=(p.prices||[]).map(price=>{
      const raw=String(price.raw_text||'');
      const numericPrice=!price.font_encoded&&price.amount!=null&&String(price.amount).trim()!==''&&Number.isFinite(Number(price.amount))&&Number(price.amount)>0;
      const missingLabel=price.font_encoded?'源站编码数字，未作为价格使用':price.price_type==='platform_reference'&&raw&&raw.length<=60?raw:/已售完/.test(raw)?'已售完':/待定/.test(raw)?'售价待定':'来源未提供数值价格';
      const value=numericPrice?`${num(price.amount)}${price.amount_high?'—'+num(price.amount_high):''} ${esc(price.unit)}`:esc(missingLabel);
      const monthOnly=!price.price_as_of&&price.period_raw&&(price.price_time_quality==='month_without_year'||/^\d{1,2}月$/.test(String(price.period_raw).trim()));
      const period=monthOnly?`${price.period_raw}（年份未披露）`:price.price_as_of||price.period_raw;
      return `<div class="price-item"><strong>${value}</strong><p class="micro">${esc(priceNames[price.price_type]||'来源参考口径')} · 价格统计期 ${text(period)}<br>${esc(price.font_encoded?'源站数字编码，未作为价格使用。':raw)}<br>采集日期不代表报价生效日期。</p>${href(price.source_url,'价格来源')}</div>`;
    }).join('');
    const permitRows=Array.isArray(p.presale_permits)?p.presale_permits:[];
    const permits=permitRows.length?`<details><summary>平台收录预售许可 · ${permitRows.length}${hi('chevron-down')}</summary><p class="micro">以下为平台收录记录，本轮未向官方许可系统核验。</p>${permitRows.map(permit=>typeof permit==='string'?`<p class="micro">${esc(permit)}</p>`:`<dl class="kv">${[['许可证号',permit?.permit_number],['发证日期',permit?.permit_date_raw],['涉及楼栋',permit?.buildings_raw]].map(([k,v])=>`<dt>${k}</dt><dd>${text(v)}</dd>`).join('')}</dl>${href(permit?.source_url,'该许可的收录页面')}`).join('')}</details>`:'';
    const propertyType=Array.isArray(p.property_type)?p.property_type.filter(Boolean).join('、'):p.property_type;
    return `<section class="section"><div class="section-header"><h3>${esc(p.name)}</h3><span class="tag gray">新房项目档案</span></div><p class="micro">包含在售与已售完历史项目；销售状态未经实时核验。不是实时房源套数，平台参考值不是备案价或网签价。</p><dl class="kv">${[['来源项目ID',p.project_id],['来源区属',p.district],['来源项目地址',p.address],['开发商',p.developer],['平台销售状态',p.sales_status],['建设状态',p.construction_status],['物业类型',propertyType],['本轮读取',p.detail_observed_at]].map(([k,v])=>`<dt>${esc(k)}</dt><dd>${text(v)}</dd>`).join('')}</dl>${p.reference_price_conflict?'<div class="notice">同一项目参考口径存在差异，保留原记录，不自动合成现价。</div>':''}${prices}${permits}${href(p.source_url,'楼盘独立详情')}${sourceButton(row.source_id)}</section>`;
  }).join('');
}
function marketSnapshotsSection(){
  const records=detail.market_snapshots||[];if(!records.length)return '';
  return `<section class="section"><h3>小区行情参考 · 非逐套成交</h3>${records.map(row=>{
    const p=row.payload||{},evidence=href(p.evidence_url,'本轮取证页面');
    const communityLink=!evidence||p.evidence_url!==p.source_url?href(p.source_url,'小区行情页面'):'';
    return `<div class="price-item"><strong>${p.reference_unit_yuan_sqm?num(p.reference_unit_yuan_sqm)+' 元/㎡':'参考均价未公开'}</strong><p class="micro">${esc(p.metric_semantics||'平台小区参考值')}<br>统计期：${text(p.source_as_of)} · 读取：${text(p.observed_at)}<br>${esc(p.time_basis||'不以采集时间代替统计日期')}</p>${evidence}${communityLink}${sourceButton(row.source_id)}</div>`;
  }).join('')}</section>`;
}

function geojson(features=[]){return {type:'FeatureCollection',features};}
function point(e,extra={}){return {type:'Feature',id:e.id,geometry:{type:'Point',coordinates:[e.lng,e.lat]},properties:{id:e.id,name:e.name,kind:e.kind,school:e.kind==='school',...extra}};}
function renderMap(){
  if(!ready||!map)return;
  const related=relatedIds().map(id=>byId.get(id)).filter(Boolean),relatedSet=new Set(related.map(e=>e.id)),selection=byId.get(selected);
  let visible=filtered.filter(located);
  if(state.markers==='selected')visible=[];
  if(state.markers==='relations')visible=[];
  if(state.markers==='evidence')visible=visible.filter(e=>e.post_count>0);
  const drawn=new Map(visible.map(e=>[e.id,e]));
  if(state.markers!=='selected')for(const e of related)if(located(e))drawn.set(e.id,e);
  if(located(selection))drawn.set(selection.id,selection);
  for(const id of compare){const e=byId.get(id);if(located(e)&&state.markers==='filtered')drawn.set(id,e);}
  const filteredSet=new Set(filtered.map(e=>e.id));
  const rankById=new Map(ranked.map(row=>[row.entity.id,row.rank]));
  const features=[...drawn.values()].map(e=>{const rank=rankById.get(e.id);return point(e,{selected:e.id===selected,related:relatedSet.has(e.id),ranking:searchMode==='ranking',number:compare.includes(e.id)?String(compare.indexOf(e.id)+1):searchMode==='ranking'&&rank<=20?String(rank):'',isResult:filteredSet.has(e.id)});});
  map.getSource('gis-places').setData(geojson(features));
  // Geographic context is independent of the query. Only-show and label controls still win.
  const surroundingHomes=contextHomes(data,state,[...drawn.keys()]);
  const homeFeatures=surroundingHomes.map(e=>point(e,{context:true,selected:false,related:false,number:'',priority:e.candidate?1:e.post_count>0?2:3}));
  map.getSource('gis-context-homes').setData(geojson(homeFeatures));
  // One label layer shares collision priorities: selected, relations, results, surroundings.
  map.getSource('gis-label-points').setData(geojson([...features,...(map.getZoom()>=13?homeFeatures:[])]));
  const plan=selected&&detail?currentRelationPlan():null;
  const lines=located(selection)&&state.markers!=='selected'?related.filter(located).map(e=>({type:'Feature',geometry:{type:'LineString',coordinates:[[selection.lng,selection.lat],[e.lng,e.lat]]},properties:{mode:plan?.tier,id:e.id}})):[];
  map.getSource('gis-connections').setData(geojson(lines));
  map.setPaintProperty('gis-connections-lines','line-dasharray',plan?.tier==='official'?[1,0]:plan?.tier==='affiliation'?[5,3]:[2,3]);
  const labelFilter=state.labels==='off'?['==',1,0]:state.labels==='selected'?['any',['get','selected'],['get','related']]:true;
  map.setFilter('gis-labels',labelFilter===true?null:labelFilter);
  const outside=selection&&!filtered.some(e=>e.id===selected)?' · 保留当前选中':'';
  const rankedLocated=ranked.filter(row=>located(row.entity)).length,topRankedLocated=ranked.slice(0,20).filter(row=>located(row.entity)).length;
  $('#map-context-text').textContent=searchMode==='ranking'?`成交排行 ${rankedLocated} 个小区可落图 · 前 20 名已定位 ${topRankedLocated} 个${outside}`:`结果与关联 ${drawn.size} 个点 · ${lines.length} 条连线${outside}${surroundingHomes.length?' · 周边小区名开启':''}`;
  $('#relationship-legend').hidden=!plan||!related.length;
  $('#relationship-legend').innerHTML=plan?`<span class="legend-swatch" data-tier="${plan.tier}"></span><span>${tierLabels[plan.tier]}${plan.tier!=='official'?' · 非对口结论':' · 地址待核'}</span>`:'';
  renderRectangle(state.rectangle);
}
function renderRectangle(b){
  if(!ready)return;
  const feature=b?{type:'Feature',geometry:{type:'Polygon',coordinates:[[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]],[b[0],b[1]]]]},properties:{}}:null;
  map.getSource('gis-selection').setData(geojson(feature?[feature]:[]));
}
function fitResults(){
  if(!map)return;
  const bound=boundsOf(filtered);if(!bound){toast('结果尚无坐标；请在左侧查看待定位记录。');return;}
  updateMapInsets();const insets=map.getPadding();
  map.fitBounds([[bound[0],bound[1]],[bound[2],bound[3]]],{padding:{top:insets.top+25,bottom:insets.bottom+25,left:insets.left+25,right:insets.right+25},maxZoom:15,duration:550});
}
function fitRelations(){
  if(!map)return;
  const entities=relatedIds().map(id=>byId.get(id));
  if(!entities.some(located)){toast('这些关联对象尚待定位，请先查看文字资料。');return;}
  const b=boundsOf([...entities,byId.get(selected)].filter(Boolean));updateMapInsets();
  const p=map.getPadding();map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:{top:p.top+32,bottom:p.bottom+32,left:p.left+32,right:p.right+32},maxZoom:15.8,duration:500});
}
function addLayers(){
  map.addSource('gis-districts',{type:'geojson',data:'./assets/districts.geojson'});
  map.addLayer({id:'gis-district-fill',type:'fill',source:'gis-districts',paint:{'fill-color':'#87956b','fill-opacity':0.035}});
  map.addLayer({id:'gis-district-lines',type:'line',source:'gis-districts',paint:{'line-color':'#829467','line-width':1.4,'line-opacity':.65,'line-dasharray':[4,3]}});
  map.addSource('gis-context-homes',{type:'geojson',data:geojson()});
  map.addLayer({id:'gis-context-home-dots',type:'circle',source:'gis-context-homes',minzoom:14,paint:{'circle-radius':2.2,'circle-color':'#977451','circle-opacity':.65,'circle-stroke-color':'#fffef8','circle-stroke-width':1}});
  map.addSource('gis-connections',{type:'geojson',data:geojson()});
  map.addLayer({id:'gis-connections-lines',type:'line',source:'gis-connections',paint:{'line-color':['match',['get','mode'],'official','#137e6d','affiliation','#7b66a2','co','#ad812d','#7a8993'],'line-width':2,'line-opacity':.72,'line-dasharray':[3,3]}});
  map.addSource('gis-places',{type:'geojson',data:geojson()});
  map.addLayer({id:'gis-point-halo',type:'circle',source:'gis-places',filter:['get','selected'],paint:{'circle-radius':17,'circle-color':['case',['get','school'],'#c44545','#569c75'],'circle-opacity':.17}});
  map.addLayer({id:'gis-points',type:'circle',source:'gis-places',paint:{'circle-radius':['case',['get','selected'],8,['get','related'],6,['!=',['get','number'],''],9,4.6],'circle-color':['case',['get','school'],['case',['get','selected'],'#9f292f',['get','related'],'#bd3d43','#c44545'],['!=',['get','number'],''],'#516d38',['get','selected'],'#154e37',['get','related'],'#128f7d','#c1945b'],'circle-stroke-color':'#fffef8','circle-stroke-width':1.8,'circle-opacity':.95}});
  map.addSource('gis-label-points',{type:'geojson',data:geojson()});
  map.addLayer({id:'gis-labels',type:'symbol',source:'gis-label-points',layout:{'text-field':['get','name'],'text-font':['Noto Sans Regular'],'text-size':['interpolate',['linear'],['zoom'],10,12,14,12,17,14],'text-variable-anchor':['top','bottom','left','right'],'text-radial-offset':.9,'text-padding':2,'text-max-width':12,'text-allow-overlap':false,'symbol-sort-key':['case',['get','selected'],-10,['get','related'],-5,['coalesce',['get','priority'],0]]},paint:{'text-color':['case',['get','selected'],'#274d31',['get','related'],'#216e54',['==',['get','context'],true],'#76624d','#65715a'],'text-halo-color':'#fffef6','text-halo-width':2}});
  map.addLayer({id:'gis-compare-numbers',type:'symbol',source:'gis-places',filter:['!=',['get','number'],''],layout:{'text-field':['get','number'],'text-font':['Noto Sans Regular'],'text-size':12,'text-allow-overlap':true},paint:{'text-color':'#fff'}});
  map.addSource('gis-selection',{type:'geojson',data:geojson()});
  map.addLayer({id:'gis-selection-fill',type:'fill',source:'gis-selection',paint:{'fill-color':'#77a46b','fill-opacity':.12}});
  map.addLayer({id:'gis-selection-line',type:'line',source:'gis-selection',paint:{'line-color':'#497d43','line-width':2,'line-dasharray':[3,2]}});
  ready=true;renderMap();
}
async function initMap(){
  try{
    const style=await get('./assets/style.json');allBaseLayers=style.layers.map(l=>l.id);
    for(const layer of style.layers)if(layer.layout?.['text-field'])layer.layout['text-size']=minimumTextSize(layer.layout['text-size']);
    // One camera and renderer for base tiles, labels, points, lines and boundaries.
    map=new maplibregl.Map({container:'map',style,center:[120.182,30.199],zoom:12.6,minZoom:8.5,maxZoom:20,pitch:0,bearing:0,dragRotate:false,pitchWithRotate:false,canvasContextAttributes:{preserveDrawingBuffer:true},attributionControl:false});
    map.touchZoomRotate.disableRotation();
    map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
    map.addControl(new maplibregl.ScaleControl({maxWidth:80,unit:'metric'}),'bottom-left');
    map.addControl(new maplibregl.AttributionControl({compact:true}),'bottom-right');
    for(const [selector,name,label]of [['.maplibregl-ctrl-zoom-in','plus','放大地图'],['.maplibregl-ctrl-zoom-out','minus','缩小地图'],['.maplibregl-ctrl-attrib-button','info','底图版权信息']]){
      const control=$(selector);control.setAttribute('aria-label',label);control.title=label;
      (control.querySelector('.maplibregl-ctrl-icon')||control).replaceChildren(createIcon(name));
    }
    map.on('style.load',addLayers);
    map.on('error',e=>{if(e.error?.message?.includes('Failed to fetch')||e.error?.message?.includes('AJAXError')){map.hasGISNetworkError=true;announce('部分在线底图资源加载失败；本地资料仍可查询。可关闭在线底图，保留研究点位与边界。');}});
    map.on('moveend',()=>{if(state.viewportOnly){const b=map.getBounds();state.bounds=[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()];renderQuery();}});
    map.on('zoomend',()=>renderMap());
    map.on('click',event=>{
      if(drawing){if(!drawStart){drawStart=[event.lngLat.lng,event.lngLat.lat];announce('再点击地图一个角，完成矩形圈选。');}else{const b=rectangle(drawStart,[event.lngLat.lng,event.lngLat.lat]);drawing=false;drawStart=null;map.getCanvas().style.cursor='';$('#rectangle-button').classList.remove('active');change({rectangle:b});}return;}
      const hits=map.queryRenderedFeatures(event.point,{layers:['gis-points','gis-labels','gis-context-home-dots']});if(hits[0])selectEntity(hits[0].properties.id);
    });
    map.on('mousemove',event=>{if(drawing){map.getCanvas().style.cursor='crosshair';if(drawStart)renderRectangle(rectangle(drawStart,[event.lngLat.lng,event.lngLat.lat]));}else if(ready)map.getCanvas().style.cursor=map.queryRenderedFeatures(event.point,{layers:['gis-points','gis-labels','gis-context-home-dots']}).length?'pointer':'';});
    new ResizeObserver(()=>updateMapInsets()).observe($('.map-stage'));
    new ResizeObserver(()=>updateMapInsets()).observe($('#detail'));
    const chromeObserver=new ResizeObserver(updateMapChrome);
    for(const el of [$('.map-toolbar'),$('#map-message'),$('.time-dock')])chromeObserver.observe(el);
    updateMapChrome();
    window.addEventListener('resize',()=>{map.resize();updateMapInsets();});
    window.visualViewport?.addEventListener('resize',()=>{map.resize();updateMapInsets();});
    // Read-only diagnostics are kept small for reproducible browser QA.
    window.gisDebug={get map(){return map;},get state(){return {...state};},get selected(){return selected;},get filteredIds(){return filtered.map(e=>e.id);},get relatedIds(){return relatedIds();},get relationPlan(){return selected&&detail?currentRelationPlan():null;},get ready(){return ready;},get counts(){return data.meta.metrics;}};
  }catch(e){$('#map').innerHTML=`<div class="error-cover"><h3>地图暂不可用</h3><p>${esc(e.message)}</p><p>请确认浏览器支持 WebGL。左侧检索、关联详情和本地资料仍可使用。</p></div>`;}
}
async function setSnapshot(index){
  const dates=data.meta.metrics.snapshot_dates,date=dates[index];if(!date)return;
  const ticket=(setSnapshot.ticket||0)+1;setSnapshot.ticket=ticket;
  $('#snapshot-label').textContent=date+' · 载入中';
  try{
    const result=await get('/api/snapshot?date='+encodeURIComponent(date));if(ticket!==setSnapshot.ticket)return;
    snapshotData=date===data.meta.metrics.candidate_snapshot?null:Object.fromEntries(result.records.map(r=>[r.entity_id,r.payload]));
    state.snapshot=date;$('#snapshot-label').textContent=date;$('#snapshot').value=index;renderQuery();renderDetail();
  }catch(e){$('#snapshot-label').textContent=state.snapshot;toast(e.message);}
}
function updateMapChrome(){
  const stage=$('.map-stage'),rect=stage.getBoundingClientRect(),toolbar=$('.map-toolbar').getBoundingClientRect();
  stage.style.setProperty('--map-toolbar-bottom',`${Math.round(toolbar.bottom-rect.top+10)}px`);
  const message=$('#map-message');
  stage.style.setProperty('--map-controls-top',`${Math.round(Math.max(toolbar.bottom,message.hidden?0:message.getBoundingClientRect().bottom)-rect.top+12)}px`);
}
function openDialog(id){
  closeSelectMenu();const d=$('#'+id);
  if(!d.hasAttribute('aria-labelledby')){const heading=d.querySelector('h2');if(heading){heading.id=id+'-title';d.setAttribute('aria-labelledby',heading.id);}}
  if(!d.open){d._returnFocus=document.activeElement;d.showModal();}
}
function focusSearch(){
  closeSelectMenu();$('#explorer').classList.add('mobile-open');document.body.classList.add('explorer-open');
  $('#search').focus();$('#search').select();
}
function sourceAccess(s){
  const status=s.link_status?.status;
  const note=status==='unreachable'?'<strong>原站当前无法访问</strong>':status==='unverified'?'<strong>原站可用性待复核</strong>':'';
  return `${note?`<div class="notice warning source-status">${note}<p>${esc(s.link_status.detail||'尚未确认原文链接当前可用。')}</p>${s.link_status.checked_at?`<small>复查时间 ${esc(s.link_status.checked_at)}</small>`:''}</div>`:''}
    <div class="source-access">${s.archive_available?`<button class="primary" data-source-archive="${esc(s.id)}">阅读本地抓取副本</button>`:''}${status==='unreachable'?'<span class="micro">失效反馈链接已暂停跳转；保留下方原网址用于追溯。</span>':href(s.url,status==='unverified'?'原站链接（可用性待复核）':'公开原始来源')}</div>
    ${s.url?`<code class="source-original-url">${esc(s.url)}</code>`:''}`;
}
async function showSourceArchive(id){
  openDialog('archive-dialog');$('#archive-content').innerHTML=empty('正在读取本地副本','不访问外部网站');
  const ticket=(showSourceArchive.ticket||0)+1;showSourceArchive.ticket=ticket;
  try{
    const archive=await get('/api/source-archive?id='+encodeURIComponent(id));
    if(ticket!==showSourceArchive.ticket||!$('#archive-dialog').open)return;
    $('#archive-content').innerHTML=`<h3>${esc(archive.title)}</h3><div class="notice warning"><strong>历史抓取副本，不是当前原站确认</strong><p>${esc(archive.limitations||'原站可用性尚未复核；只供追溯既有资料。')}</p><p>缓存时间 ${esc(archive.archived_at||'待核')} · TLS 验证：${archive.tls_verified?'已验证':'未核验或无记录'}</p></div><div class="archive-text">${esc(archive.text)}</div><details><summary>来源与完整性信息</summary><p class="source-original-url">${esc(archive.source_url)}</p><p>${esc(archive.archive_hash_scope||'原始抓取文件')}</p><code>SHA-256 ${esc(archive.archive_sha256)}</code></details>`;
  }catch(e){if($('#archive-dialog').open)$('#archive-content').innerHTML=empty('副本暂时无法读取',e.message);}
}
const sourceView={query:'',page:1,ticket:0};
function sourceCard(s,single=false){
  return `<article class="source-card${single?' focus-source':''}" data-source-card="${esc(s.id)}" tabindex="-1" aria-label="${esc(s.label)}"><h3>${esc(s.label)}</h3><p>资料截至：${text(s.source_as_of)}　读取 / 观察：${text(s.observed_at)}</p><p>${esc(s.notes)}</p>${sourceAccess(s)}<code>${esc(s.path)}</code><code>SHA-256 ${esc(s.sha256)}</code></article>`;
}
function renderSourcesPage({moveFocus=false}={}){
  const page=sourcePage(data.sources,sourceView);
  sourceView.page=page.page;
  const cards=$('#sources-cards');if(!cards)return;
  cards.innerHTML=page.items.map(s=>sourceCard(s)).join('')||empty('没有匹配的来源','试试学校、小区、来源名称或年份；也可以清空搜索查看来源目录。');
  const summary=page.matched?`显示 ${num(page.start)}—${num(page.end)} 条 · 匹配 ${num(page.matched)} / 目录 ${num(page.total)} 条来源`:`没有匹配 · 目录 ${num(page.total)} 条来源`;
  $('#sources-result-count').textContent=summary;
  cards.setAttribute('aria-label',summary);
  $('#sources-page-label').textContent=`${page.page} / ${page.pageCount} 页`;
  $('#sources-prev').disabled=page.page<=1;
  $('#sources-next').disabled=page.page>=page.pageCount;
  $('#sources-clear').disabled=!$('#sources-search').value;
  $('#sources-content').scrollTop=0;
  if(moveFocus)cards.focus({preventScroll:true});
}
async function showSources(focus=''){
  const ticket=++sourceView.ticket,content=$('#sources-content');
  sourceView.query='';sourceView.page=1;
  const current=()=>ticket===sourceView.ticket&&$('#source-dialog').open;
  $('#source-dialog .dialog-head h2').textContent=focus?'这条资料，来自哪里？':'资料有出处，判断有边界。';
  if(focus){
    content.innerHTML=`<div class="source-view-nav"><p class="micro">当前仅显示所选资料来源</p><button id="sources-show-all" type="button">查看来源目录（${num(data.sources.length)}）</button></div><div id="sources-single">${empty('正在读取这条来源','不会展开来源目录')}</div><details class="source-coverage"><summary>全库资料覆盖与判断边界</summary>${sourceCoverage()}</details>`;
    $('#sources-show-all').onclick=()=>showSources();
    openDialog('source-dialog');content.scrollTop=0;
    try{
      let source=sourcePage(data.sources,{focus}).items[0];
      if(!source)source=await get('/api/source?id='+encodeURIComponent(focus));
      if(!current())return;
      if(source?.id!==focus)throw Error('来源 ID 未匹配，未展示其他来源。');
      $('#sources-single').innerHTML=sourceCard(source,true);
      $('#sources-single .source-card').focus({preventScroll:true});
    }catch(error){
      if(current())$('#sources-single').innerHTML=empty('这条来源暂时无法读取',error.message);
    }
    return;
  }
  content.innerHTML=`<div class="sources-toolbar"><label for="sources-search">搜索来源目录</label><div class="source-search-row"><input id="sources-search" type="search" autocomplete="off" placeholder="学校、小区、来源名称、年份或网址" aria-describedby="sources-search-help"><button id="sources-clear" type="button" disabled>清空</button></div><p id="sources-search-help" class="micro">来源目录不等于完整来源总量；逐条原帖来源可从原帖入口查看。搜索不改变地图筛选。</p><div class="source-pagination"><p id="sources-result-count" role="status" aria-live="polite" aria-atomic="true"></p><nav aria-label="来源分页"><button id="sources-prev" type="button">上一页</button><span id="sources-page-label"></span><button id="sources-next" type="button">下一页</button></nav></div></div><details class="source-coverage"><summary>全库资料覆盖与判断边界</summary>${sourceCoverage()}</details><div id="sources-cards" tabindex="-1"></div>`;
  const input=$('#sources-search');
  const search=()=>{
    if(!current()||sourceView.query===input.value)return;
    sourceView.query=input.value;sourceView.page=1;renderSourcesPage();
  };
  input.addEventListener('input',event=>{if(!event.isComposing)search();});
  input.addEventListener('compositionend',search);
  $('#sources-clear').onclick=()=>{input.value='';sourceView.query='';sourceView.page=1;renderSourcesPage();input.focus({preventScroll:true});};
  $('#sources-prev').onclick=()=>{sourceView.page--;renderSourcesPage({moveFocus:true});};
  $('#sources-next').onclick=()=>{sourceView.page++;renderSourcesPage({moveFocus:true});};
  openDialog('source-dialog');renderSourcesPage();input.focus({preventScroll:true});
}
function sourceCoverage(){
  const m=data.meta.metrics,kinds=m.price_kinds||{},inc=m.incremental||{},listingCount=Number(kinds.listing)||0,referenceCount=Number(kinds.reference)||0;
  const years=(m.years||[]).join(' / '),projects=Number(m.projects)||0,snapshots=Number(m.market_snapshots)||0;
  const completeness=m.deal_completeness,dealCoverage=completeness?`已入库成交记录：${num(completeness.fully_priced)} 条完整价格、${num(completeness.price_incomplete)} 条价格缺项；完整价格样本的最晚成交日期 ${esc(completeness.latest_fully_priced_date||'未知')}。`:'成交记录包括平台未公开或打码价格的条目；完整价格覆盖尚未统计。';
  const incrementNotice=inc.source_manifest?`本轮已入库：${num(inc.projects||0)} 个新房项目档案、${num(inc.deals||0)} 条成交记录、${num(inc.listings||0)} 条挂牌线索、${num(inc.market_snapshots||0)} 条小区行情快照。另有 ${num(inc.deal_price_enrichments||0)} 条已有成交补价，不增加交易条数；${num(inc.official_local_policies||0)} 份本地政策归档，不算新核实的学校—小区关系。小红书新增有效正文 ${num(inc.social_new_bodies||0)} / 1,000 条，其中新帖子索引 ${num(inc.social_new_posts||0)} 条；其余为既有索引补读。仅显示已验收快照，不把正在采集的数据计为已入库。采集到的历史记录不等于本轮新发生交易。`:'当前构建尚未接入已验收增量包；磁盘上的采集文件不计入上述覆盖。';
  return `<div class="source-grid">${[[m.posts,'条原帖索引'],[m.post_details,'条正文 / 说明'],[m.candidates,'行候选记录'],[m.school_records,'条年度学校 / 校区档案'],[m.admissions,'条年度招生明细'],[kinds.deal||0,'条历史成交记录（含缺价）'],[listingCount,'条挂牌线索'],[referenceCount,'条参考价记录'],[projects,'个新房项目档案'],[snapshots,'条小区行情快照']].map(([n,label])=>`<div class="metric-cell"><strong>${num(n)}</strong><label>${esc(label)}</label></div>`).join('')}</div><div class="notice"><b>先看时间与覆盖</b><br>学校源所标统计截止月 ${esc(m.school_as_of||'未提供')}，档案年度 ${esc(years||'待核')}；各条招生明细保留原始年度，不跨年推断对口，也不是今日官方复核。已接入成交记录的最晚事件日期 ${esc(m.deal_as_of||'未知')}。当前候选快照 ${esc(m.candidate_snapshot||'未知')}。<br>挂牌 / 参考价共 ${num(listingCount+referenceCount)} 条 = ${num(listingCount)} 条挂牌线索 + ${num(referenceCount)} 条参考价记录；另有 ${num(projects)} 个新房项目档案、${num(snapshots)} 条小区行情快照。项目、记录与房源是不同粒度，不能相加为在售套数。<br>${dealCoverage}缺价不等于没有成交，疑似重复不算作已确认独立房屋。参考统计期未知时保持未知，读取日期不当报价日期。</div><div class="notice"><b>本轮增量</b><br>${incrementNotice}<br>新房项目可能含已售完历史项目，平台参考价不是备案价或网签成交价；小区行情快照不是逐套成交。</div><div class="notice positive"><b>地点与关系的可用范围</b><br>已接入滨江、拱墅 ${num(m.school_records)} 条年度学校 / 校区档案和 ${num(m.admissions)} 条年度招生明细；${num(m.school_campus_links)} 条学校—地图校区近邻 / 沿革桥接，${num(m.official_located_school_records)} / ${num(m.school_records)} 条学校档案可关联地图点位，不等于校区身份已核验。<br>官方明细合并到 ${num(m.official_home_entities)} 个名称关联实体，其中 ${num(m.official_located_home_entities)} 个有地图点位；名称、校区、分期和坐标仍需地址复核。<br>直接名单未命中时，详情面板显示分级核验线索，同帖、附近或同区入口明确标注“不是对口结论”。全库 ${num(m.unlocated_entities)} 个未定位对象继续保留在列表。</div><p class="micro">名称匹配不是地址核验；同帖关系不是官方招生，附近不是学区。行政边界来自 OSM，不绘制推测的学校服务区多边形。${num(m.snapshot_dates?.length||0)} 个候选日快照仅回放候选观察，不是整库时光机。</p><p class="micro">本地只读索引构建于 ${esc(data.meta.built_at)}。原始文件未移动、未改写；未变更任何自动化调度。更新需重新运行本地构建脚本，失败保留上一份数据库。</p>`;
}
async function showCompare(){
  openDialog('compare-dialog');$('#compare-content').innerHTML=empty('正在连接比较资料','');
  if(compare.length<2){$('#compare-content').innerHTML=empty('选择 2—4 个小区开始比较','在小区详情点击“加入比较”。编号会同步固定在地图上。');return;}
  try{
    const details=await Promise.all(compare.map(async id=>{const d=detailCache.get(id)||await get('/api/entity?id='+encodeURIComponent(id));detailCache.set(id,d);return d;}));
    const cells=compare.map((id,i)=>({e:byId.get(id),d:details[i],c:getCandidate(byId.get(id))}));
    const fields=[['点位核验',x=>located(x.e)?'OSM 对象中心；名称关联待核':'待定位，未画点'],['官方学校',x=>{const plan=relationPlan(data,x.d,x.e.id,state,'official',{snapshot:snapshotData}),ids=plan.tier==='official'?plan.items.map(r=>r.id):[];return ids.length?ids.map(id=>`<button data-select="${esc(id)}" data-dismiss="compare-dialog">${esc(byId.get(id)?.name||id)} ${hi('arrow-right')}</button>`).join('<br>'):`没有 ${esc(state.year)} 年名单关系，未知`; }],['研究单价',x=>x.c?`${num(x.c.unit_price)} 元/㎡<br>候选观察，不是今日报价`:'未知'],['估算总价',x=>text(x.c?.total_range)],['建成年份',x=>text(x.c?.built_year)],['历史成交',x=>{const p=x.d.prices.filter(p=>p.kind==='deal'&&(!state.dealFrom||p.event_date>=state.dealFrom)&&(!state.dealTo||p.event_date<=state.dealTo));return `${p.length} 条样本<br>截至 ${esc(data.meta.metrics.deal_as_of)}`;}],['挂牌 / 参考价',x=>`${x.d.prices.filter(p=>p.kind==='listing').length} 条挂牌线索<br>${x.d.prices.filter(p=>p.kind==='reference').length} 条小区参考价`],['轨交参考',x=>text(x.c?.nearest_metro||x.c?.metro_text)],['风险与缺口',x=>text(x.c?.risk||'未有候选风险记录；不等于没有风险')],['原帖证据',x=>`${x.d.posts.length} 条${sourceButton('posts','口径')}`]];
    $('#compare-content').innerHTML=`<p class="micro">招生年度 ${esc(state.year)} · ${esc(state.admission)} · 候选观察 ${esc(state.snapshot)}。未知不补分；地图编号与这里一致。</p><div style="overflow:auto"><table class="comparison-table"><thead><tr><th>比较维度</th>${cells.map((x,i)=>`<th><span class="number-pin">${i+1}</span>${esc(x.e.name)}<br><button data-select="${esc(x.e.id)}" data-dismiss="compare-dialog">地图 / 完整档案 ${hi('arrow-right')}</button> <button data-remove-compare="${esc(x.e.id)}">移除</button></th>`).join('')}</tr></thead><tbody>${fields.map(([label,fn])=>`<tr><td>${esc(label)}</td>${cells.map(x=>`<td>${fn(x)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }catch(e){$('#compare-content').innerHTML=empty('比较资料读取失败',e.message);}
}
function showWorkspace(){
  let draft={};try{draft=JSON.parse(read('draft'))||{};}catch{}
  const fields=[['buy','计划买入总价'],['sale','计划卖出总价'],['cash','可投入现金'],['cost','预留税费 / 支出'],['debt','待偿还本金']];
  $('#workspace-content').innerHTML=`<div class="notice positive">仅保存在当前浏览器的本地存储。不会自动读取项目中的家庭身份、孩子或资产信息，不写入地址栏，不发送给底图服务。</div><h3>手动填写资金草稿 · 单位：万元</h3><form id="draft-form" class="workspace-form"><div class="field-row">${fields.map(([key,label])=>`<label>${label}<input name="${key}" type="number" min="0" step="0.01" value="${esc(draft[key]??'')}" placeholder="请填写，包括 0"></label>`).join('')}</div><label>本地备注<textarea name="note" maxlength="1000" placeholder="保留你的假设与待核验事项">${esc(draft.note||'')}</textarea></label><div id="draft-output" class="draft-output">填完整五项后显示简单资金差额。</div><button type="submit" class="primary">保存到本机</button> <button type="button" id="clear-draft">清除此草稿</button></form><p class="micro">仅计算：买入 + 预留支出 −（卖出 − 待偿本金 + 现金）。不计算贷款资格、利率或税务适用性，不是融资或入学结论。</p><section class="section"><div class="section-header"><h3>我的收藏</h3><small>${favorites.length} 个地点</small></div><div class="favorites-grid">${favorites.map(id=>`<button class="relation-item" data-select="${esc(id)}" data-dismiss="workspace-dialog">${esc(byId.get(id)?.name||id)} ${hi('arrow-right')}</button>`).join('')||'<p class="micro">在地点详情点击收藏。收藏不会改变原始资料。</p>'}</div></section>`;
  openDialog('workspace-dialog');calculateDraft();
}
function calculateDraft(){
  const form=$('#draft-form');if(!form)return;
  const result=fundingGap(Object.fromEntries(new FormData(form)));$('#draft-output').innerHTML=result?`卖出净额 ${num(result.netSale,2)} 万 · 可投入 ${num(result.available,2)} 万<br>${result.gap>0?'尚需资金':'余量'} <strong>${num(Math.abs(result.gap),2)} 万</strong>`:'请完整填写五项，未知金额不会按 0 计算。';
}

function bindEvents(){
  let searchTimer;
  $('#search').addEventListener('input',e=>{state.query=e.target.value;$('#search-clear').hidden=!state.query;pageSize=40;clearTimeout(searchTimer);searchTimer=setTimeout(()=>searchMode==='posts'?searchPosts():renderQuery(),140);});
  for(const key of Object.keys(DEFAULTS)){if(['query','bounds','rectangle','snapshot','schoolId'].includes(key))continue;const input=document.getElementById(key);if(!input)continue;
    input.addEventListener('change',()=>{let value=input.type==='checkbox'?input.checked:input.value;const patch={[key]:value};
      if(key==='viewportOnly'&&value&&map){const b=map.getBounds();patch.bounds=[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()];}
      if(key==='kind'||key==='district')patch.schoolId='';
      if(['dealFrom','dealTo'].includes(key)&&value)patch.priceKind='deal';
      change(patch,{fit:key==='district'&&!state.viewportOnly});if(['dealFrom','dealTo'].includes(key))renderDetail();
    });
  }
  document.addEventListener('click',async e=>{
    const b=e.target.closest('button');if(!b)return;
    if(b.dataset.select){if(b.dataset.dismiss)$('#'+b.dataset.dismiss).close();return selectEntity(b.dataset.select);}
    if(b.dataset.source)return showSources(b.dataset.source);
    if(b.dataset.sourceArchive)return showSourceArchive(b.dataset.sourceArchive);
    if(b.dataset.closeDialog)return $('#'+b.dataset.closeDialog).close();
    if(b.dataset.district!==undefined){change({district:b.dataset.district,schoolId:'',rectangle:null,viewportOnly:false},{fit:true});return;}
    if(b.dataset.searchMode){
      if(searchMode===b.dataset.searchMode)return;
      if(searchMode==='places')placesKind=state.kind;
      searchMode=b.dataset.searchMode;
      if(searchMode==='posts')state.kind=state.schoolId?'residential':'all';
      else if(searchMode==='ranking'){
        state.kind='residential';state.priceKind='deal';state.schoolId='';state.officialOnly=false;
        const asOf=data.meta.metrics.deal_as_of||'';
        if(!state.dealFrom&&!state.dealTo&&/^\d{4}-\d{2}-\d{2}$/.test(asOf)){state.dealFrom=asOf.slice(0,4)+'-01-01';state.dealTo=asOf;}
      }else state.kind=placesKind;
      syncInputs();renderDetail();
      if(searchMode==='posts')return searchPosts();
      renderQuery();
      if(searchMode==='ranking'&&!state.viewportOnly)fitResults();
      return;
    }
    if(b.dataset.clearFilter){change({[b.dataset.clearFilter]:DEFAULTS[b.dataset.clearFilter]});if(b.dataset.clearFilter==='query'&&searchMode==='posts')searchPosts();return;}
    if(b.dataset.relation){detailMode=b.dataset.relation;relationQuery='';relationLocatedOnly=false;renderDetail();renderMap();return;}
    if(b.dataset.priceMarket){priceMarketFilter=b.dataset.priceMarket;renderDetail();return;}
    if(b.dataset.detailTab){detailTab=b.dataset.detailTab;renderDetail();$('.detail-body').scrollTop=0;$('#detail-tab-'+detailTab)?.focus({preventScroll:true});return;}
    if(b.dataset.removeCompare){compare=compare.filter(id=>id!==b.dataset.removeCompare);save('compare',compare);syncInputs();renderMap();renderDetail();return showCompare();}
    switch(b.id){
      case 'load-more':pageSize+=40;renderQuery();break;
      case 'reset':state={...DEFAULTS,snapshot:data.meta.metrics.candidate_snapshot};searchMode='places';placesKind=DEFAULTS.kind;rankingMetric='count';snapshotData=null;drawing=false;drawStart=null;$('#rectangle-button').classList.remove('active');$('#snapshot').value=data.meta.metrics.snapshot_dates.length-1;$('#snapshot-label').textContent=state.snapshot;syncInputs();renderDetail();renderQuery();fitResults();break;
      case 'clear-school':change({schoolId:''});break;
      case 'scope-school':if(!currentRelationPlan().canScope)break;searchMode='places';placesKind='residential';change({schoolId:selected,kind:'residential',query:'',district:byId.get(selected).district,officialOnly:false,rectangle:null,viewportOnly:false},{fit:true});break;
      case 'fit-relations':fitRelations();break;
      case 'show-relations':change({markers:state.markers==='relations'?'filtered':'relations'});break;
      case 'inspector-collapse':document.body.classList.toggle('detail-collapsed');renderDetail();break;
      case 'close-detail':closeDetail();break;
      case 'back-detail':back();break;
      case 'locate-entity':{const x=byId.get(selected);if(located(x))map?.easeTo({center:[x.lng,x.lat],zoom:16,duration:400});break;}
      case 'favorite-entity':favorites=favorites.includes(selected)?favorites.filter(id=>id!==selected):[...favorites,selected];save('favorites',favorites);renderDetail();renderQuery();break;
      case 'compare-entity':if(compare.length===4&&!compare.includes(selected)){toast('最多比较 4 个小区，请先移除一个。');break;}compare=toggleCompare(compare,selected);save('compare',compare);syncInputs();renderDetail();renderMap();break;
      case 'compare-button':showCompare();break;
      case 'sources-button':showSources();break;
      case 'workspace-button':showWorkspace();break;
      case 'shortcuts-button':openDialog('shortcuts-dialog');break;
      case 'search-shortcut':focusSearch();break;
      case 'search-clear':clearTimeout(searchTimer);state.query='';syncInputs();searchMode==='posts'?searchPosts():renderQuery();$('#search').focus();break;
      case 'layers-button':$('#layer-panel').hidden=!$('#layer-panel').hidden;b.setAttribute('aria-expanded',String(!$('#layer-panel').hidden));break;
      case 'fit-button':fitResults();break;
      case 'rectangle-button':drawing=!drawing;drawStart=null;b.classList.toggle('active',drawing);renderQuery();break;
      case 'clear-rectangle':change({rectangle:null});break;
      case 'snapshot-latest':setSnapshot(data.meta.metrics.snapshot_dates.length-1);break;
      case 'mobile-explorer':$('#explorer').classList.toggle('mobile-open');document.body.classList.toggle('explorer-open',$('#explorer').classList.contains('mobile-open'));break;
      case 'clear-draft':openDialog('confirm-dialog');break;
      case 'confirm-clear-draft':try{localStorage.removeItem(savedKey+'draft');$('#confirm-dialog').close();showWorkspace();toast('本地资金草稿已清除，无法撤销。');}catch{toast('浏览器存储不可用；草稿未清除。');}break;
    }
  });
  $('#snapshot').addEventListener('change',e=>setSnapshot(Number(e.target.value)));
  $('#snapshot').addEventListener('input',e=>$('#snapshot-label').textContent=data.meta.metrics.snapshot_dates[Number(e.target.value)]);
  $('#rankingMetric').addEventListener('change',e=>{rankingMetric=e.target.value;pageSize=40;renderQuery();});
  $('#show-districts').addEventListener('change',e=>{if(!ready)return;for(const id of ['gis-district-fill','gis-district-lines'])map.setLayoutProperty(id,'visibility',e.target.checked?'visible':'none');});
  $('#online-basemap').addEventListener('change',e=>{if(!ready)return;for(const id of allBaseLayers)if(map.getLayer(id))map.setLayoutProperty(id,'visibility',e.target.checked?'visible':'none');toast(e.target.checked?'已显示在线底图':'已隐藏在线底图；本地研究图层继续显示。');});
  document.addEventListener('input',e=>{if(e.target.closest('#draft-form'))calculateDraft();if(e.target.id==='relation-search'){relationQuery=e.target.value;updateRelationList();}});
  document.addEventListener('change',e=>{if(e.target.id==='relation-located-only'){relationLocatedOnly=e.target.checked;updateRelationList();}});
  document.addEventListener('click',e=>{if(!e.target.closest('#layer-panel,#layers-button,.select-popover')){$('#layer-panel').hidden=true;$('#layers-button').setAttribute('aria-expanded','false');}});
  for(const dialog of $$('dialog'))dialog.addEventListener('close',()=>{closeSelectMenu();if(dialog._returnFocus?.isConnected)dialog._returnFocus.focus({preventScroll:true});});
  document.addEventListener('submit',e=>{if(e.target.id!=='draft-form')return;e.preventDefault();const draft=Object.fromEntries(new FormData(e.target));if(save('draft',draft))toast('资金草稿已保存在本机浏览器。');});
  document.addEventListener('keydown',async e=>{
    if(e.defaultPrevented||e.isComposing||$('dialog[open]')||isSelectMenuOpen())return;
    const editable=e.target.closest('input,textarea,select,[contenteditable=true],[role=combobox]');
    if((e.ctrlKey||e.metaKey)&&!e.altKey&&e.key.toLowerCase()==='k'){e.preventDefault();focusSearch();return;}
    if(e.ctrlKey||e.metaKey||e.altKey)return;
    if(e.target.id==='search'&&['ArrowDown','Enter'].includes(e.key)){
      e.preventDefault();clearTimeout(searchTimer);if(searchMode==='posts')await searchPosts();else renderQuery();
      const first=$('#results [data-select]');if(e.key==='Enter')first?.click();else first?.focus();return;
    }
    if(e.target.matches('.result-card,.ranking-card')&&['ArrowUp','ArrowDown','Home','End'].includes(e.key)){
      e.preventDefault();const items=$$('#results .result-card,#results .ranking-card'),i=items.indexOf(e.target),next=e.key==='Home'?0:e.key==='End'?items.length-1:Math.max(0,Math.min(items.length-1,i+(e.key==='ArrowDown'?1:-1)));items[next]?.focus();return;
    }
    if(e.target.matches('[data-detail-tab]')&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
      e.preventDefault();const tabs=$$('[data-detail-tab]'),index=tabs.indexOf(e.target),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(index+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();return;
    }
    if(!editable&&e.key==='/'){e.preventDefault();focusSearch();return;}
    if(!editable&&e.key.toLowerCase()==='r'){e.preventDefault();document.querySelector('[data-search-mode="ranking"]')?.click();return;}
    if(!editable&&e.key==='?'){e.preventDefault();openDialog('shortcuts-dialog');return;}
    if(!editable&&['+','=','-'].includes(e.key)&&ready){e.preventDefault();e.key==='-'?map.zoomOut():map.zoomIn();return;}
    if(e.key==='Escape'){
      if(e.target.id==='search'&&state.query){e.preventDefault();$('#search-clear').click();}
      else if(editable){e.target.blur();}
      else if(drawing){drawing=false;drawStart=null;$('#rectangle-button').classList.remove('active');renderQuery();}
      else if(!$('#layer-panel').hidden){$('#layer-panel').hidden=true;$('#layers-button').setAttribute('aria-expanded','false');$('#layers-button').focus();}
      else if($('#explorer').classList.contains('mobile-open')){$('#explorer').classList.remove('mobile-open');document.body.classList.remove('explorer-open');}
      else if(selected){closeDetail();$('#search').focus();}
    }
  });
  window.addEventListener('hashchange',()=>{const id=new URLSearchParams(location.hash.slice(1)).get('place');if(id&&byId.has(id)&&id!==selected)selectEntity(id);else if(!id&&selected)closeDetail();});
}
async function init(){
  try{
    for(const button of $$('[data-close-dialog]'))if(button.getAttribute('aria-label'))button.replaceChildren(createIcon('close'));
    $('#shortcuts-button').replaceChildren(createIcon('help'));
    enhanceIcons();
    data=await get('/api/bootstrap');byId=new Map(data.entities.map(e=>[e.id,e]));
    $('#advanced-deal-as-of').textContent=data.meta.metrics.deal_as_of||'未知';
    const valid=new Set(byId.keys());favorites=readSaved(read('favorites'),valid);compare=readSaved(read('compare'),valid).filter(id=>byId.get(id).kind==='residential').slice(0,4);
    state.snapshot=data.meta.metrics.candidate_snapshot;$('#snapshot-label').textContent=state.snapshot;$('#snapshot').max=data.meta.metrics.snapshot_dates.length-1;$('#snapshot').value=$('#snapshot').max;
    $('#build-date').textContent=data.meta.built_at.slice(0,10);bindEvents();syncInputs();enhanceSelects();renderQuery();await initMap();
    const id=new URLSearchParams(location.hash.slice(1)).get('place');if(id&&byId.has(id))selectEntity(id,{push:false});
  }catch(e){$('#results').innerHTML=empty('资料库未就绪',e.message+'。请运行本地数据构建脚本后刷新。');announce('资料加载失败；未使用空数据冒充正常结果。');}
}
init();
