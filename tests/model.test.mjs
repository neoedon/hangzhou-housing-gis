import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULTS,normalize,numeric,located,inside,rectangle,relatedAdmissions,canonicalSchoolIds,campusSchoolIds,queryEntities,relationPlan,contextHomes,priceMarket,communityProfile,toggleCompare,readSaved,fundingGap,boundsOf,isFresh,filterPosts,minimumTextSize} from '../web/model.js';
const school={id:'s',name:'闻涛小学',kind:'school',primary_school:1,district:'滨江区',lat:30.2,lng:120.2,official_years:['2026']};
const house={id:'h',name:'闻涛花园',kind:'residential',district:'滨江区',lat:30.201,lng:120.201,candidate:{rank:1,built_year:2008,unit_price:30000},price_filter:[{kind:'listing',total_wan:300,area_sqm:89,observed_at:'2026-08-18'},{kind:'deal',total_wan:200,area_sqm:70,event_date:'2026-06-23'}]};
const unlocated={id:'u',name:'待定小区',kind:'residential',district:'滨江区',lat:null,lng:null};
const campus={id:'c',name:'闻涛旧名校区',kind:'school',primary_school:0,district:'滨江区',lat:30.2,lng:120.19,official_years:['2026']};
const data={entities:[school,campus,house,unlocated,{id:'g',name:'拱墅小学',kind:'school',primary_school:1,district:'拱墅区',lat:30.3,lng:120.1}],admissions:[{school_id:'s',home_id:'h',year:'2026',admission_type:'户籍生',active:1},{school_id:'s',home_id:'u',year:'2026',admission_type:'新杭州人',active:1},{school_id:'s',home_id:'u',year:'2024',admission_type:'户籍生',active:1}],school_campus_links:[{campus_id:'c',official_school_id:'s',year:'2026'}]};
const q=(patch={})=>queryEntities(data,{...DEFAULTS,...patch},[],null,'2026-09-08').map(e=>e.id);
test('normalization keeps campus and phase qualifiers',()=>{assert.equal(normalize(' 月明（校区） '),'月明(校区)');assert.notEqual(normalize('闻涛西区'),normalize('闻涛东区'));});
test('minimum text size preserves zoom expression and clamps outputs',()=>assert.deepEqual(minimumTextSize(['interpolate',['linear'],['zoom'],8,9,12,11,17,16]),['interpolate',['linear'],['zoom'],8,12,12,12,17,16]));
test('blank or negative numbers are not price zero',()=>{assert.equal(numeric(''),null);assert.equal(numeric(null),null);assert.equal(numeric(-1),null);assert.equal(numeric('0'),0);});
test('default filters schools in Binjiang only',()=>assert.deepEqual(q(),['s']));
test('local aliases and name search',()=>assert.deepEqual(q({kind:'all',query:'闻 涛'}),['s','h']));
test('source project names are searchable without renaming the geographic entity',()=>{
  const projectData={...data,entities:[{...house,project_names:['滨江·映运轩']} ]};
  assert.deepEqual(queryEntities(projectData,{...DEFAULTS,kind:'residential',query:'滨江映运轩'}).map(e=>e.id),[house.id]);
  assert.equal(projectData.entities[0].name,house.name);
});
test('future admission year is not backfilled',()=>assert.deepEqual(q({officialOnly:true,year:'2029'}),[]));
test('school-to-home year and admission filters',()=>{assert.deepEqual(q({kind:'residential',schoolId:'s',admission:'户籍生'}),['h']);assert.deepEqual(q({kind:'residential',schoolId:'s',year:'2024'}),['u']);});
test('verified campus link inherits canonical official relations',()=>{assert.deepEqual([...canonicalSchoolIds(data,'c','2026')],['c','s']);assert.deepEqual(campusSchoolIds(data,'s','2026'),['c']);assert.equal(relatedAdmissions(data,'c',{...DEFAULTS}).length,2);assert.deepEqual(q({kind:'residential',schoolId:'c',admission:'户籍生'}),['h']);});
test('no official record does not fabricate negative relationship',()=>assert.deepEqual(relatedAdmissions(data,'g',{...DEFAULTS}),[]));
test('unlocated objects remain queryable, but not on spatial filters',()=>{assert.deepEqual(q({kind:'residential',located:'no'}),['u']);assert.equal(located(unlocated),false);assert.equal(inside(unlocated,[120,30,121,31]),false);});
test('rectangle handles reverse drag',()=>{assert.deepEqual(rectangle([121,31],[120,30]),[120,30,121,31]);assert.deepEqual(q({kind:'all',rectangle:[120.199,30.199,120.202,30.202]}),['s','h']);});
test('viewport requires explicit switch',()=>{assert.deepEqual(q({bounds:[0,0,1,1]}),['s']);assert.deepEqual(q({viewportOnly:true,bounds:[0,0,1,1]}),[]);});
test('budget and area must match SAME record',()=>{assert.deepEqual(q({kind:'residential',maxBudget:'250',minArea:'80'}),[]);assert.deepEqual(q({kind:'residential',maxBudget:'310',minArea:'80'}),['h']);});
test('unknown prices excluded unless explicitly retained',()=>{assert.deepEqual(q({kind:'residential',maxBudget:'250'}),[]);assert.deepEqual(q({kind:'residential',maxBudget:'250',includeUnknown:true}),['u']);});
test('transaction dates are independent of candidate snapshot',()=>{assert.deepEqual(q({kind:'residential',priceKind:'deal',dealFrom:'2026-07-01'}),[]);assert.deepEqual(q({kind:'residential',priceKind:'deal',dealTo:'2026-06-30'}),['h']);});
test('old observation cannot turn fresh by rebuilding app',()=>{assert.equal(isFresh('2026-06-23','2026-09-08'),false);assert.equal(isFresh('2026-09-01','2026-09-08'),true);assert.deepEqual(q({kind:'residential',priceKind:'deal',freshOnly:true}),[]);});
test('snapshot candidate filter does not use current candidate',()=>{const result=queryEntities(data,{...DEFAULTS,kind:'residential',candidateOnly:true},[],{});assert.deepEqual(result,[]);});
test('comparison bounded to four and removable',()=>{assert.deepEqual(toggleCompare(['a','b','c','d'],'e'),['a','b','c','d']);assert.deepEqual(toggleCompare(['a','b'],'a'),['b']);});
test('stored ids validated and deduplicated',()=>{assert.deepEqual(readSaved('["s","s","invalid",3]',new Set(['s'])),['s']);assert.deepEqual(readSaved('broken',new Set()),[]);});
test('funding draft requires all inputs including explicit zeros',()=>{assert.equal(fundingGap({buy:300,sale:200,cash:10,cost:0}),null);assert.deepEqual(fundingGap({buy:300,sale:200,cash:10,cost:0,debt:50}),{netSale:150,available:160,gap:140});});
test('bounds ignore missing coordinates',()=>assert.deepEqual(boundsOf([school,house,unlocated]),[120.2,30.2,120.201,30.201]));
test('school budget filters travel through same-year official homes',()=>{assert.deepEqual(q({maxBudget:'310',minArea:'80'}),['s']);assert.deepEqual(q({maxBudget:'250',minArea:'80'}),[]);assert.deepEqual(q({year:'2029',maxBudget:'310'}),[]);});
test('missing values never satisfy fresh-only date constraint',()=>assert.deepEqual(q({kind:'residential',freshOnly:true,includeUnknown:true,priceKind:'deal'}),[]));
test('newly observed undated references and recommendation cards are not fresh quotes',()=>{
  for(const p of [{kind:'reference',source_id:'new-project:test'},{kind:'listing',source_id:'incremental-price:fang:test'}]){
    const freshData={...data,entities:[{...house,price_filter:[{...p,total_wan:300,area_sqm:89,observed_at:'2026-09-09'}]}]};
    assert.equal(queryEntities(freshData,{...DEFAULTS,kind:'residential',priceKind:p.kind,freshOnly:true},[],null,'2026-09-09').length,0);
    freshData.entities[0].price_filter[0].price_as_of='2026-09-01';
    assert.equal(queryEntities(freshData,{...DEFAULTS,kind:'residential',priceKind:p.kind,freshOnly:true},[],null,'2026-09-09').length,1);
  }
});
test('post geographic filters use the same entity result set',()=>{const posts=[{id:'1',place_ids:['h'],district:'滨江'},{id:'2',place_ids:[],district:'滨江'},{id:'3',place_ids:['g'],district:'拱墅'}];assert.deepEqual(filterPosts(posts,data,{...DEFAULTS,kind:'all'}).map(p=>p.id),['1','2']);assert.deepEqual(filterPosts(posts,data,{...DEFAULTS,kind:'all',rectangle:[120.2,30.2,120.202,30.202]}).map(p=>p.id),['1']);assert.deepEqual(filterPosts(posts,data,{...DEFAULTS,kind:'all',minBudget:'100'}).map(p=>p.id),['1']);});

const portalData={
  entities:[{...school,lat:null,lng:null,name:'杭州市闻涛实验小学（本部）'},
    {...campus,primary_school:1,name:'闻涛实验小学'},house,unlocated,
    {id:'old',name:'闻涛旧校区',kind:'school',district:'滨江区',primary_school:1,lat:30.21,lng:120.21,official_years:['2026']}],
  admissions:data.admissions,
  school_campus_links:[
    {campus_id:'c',official_school_id:'s',year:'2026',kind:'portal_coordinate_match',distance_m:12},
    {campus_id:'old',official_school_id:'s',year:'2026',kind:'documented_campus_affiliation'}]
};
test('official canonical search resolves to usable physical campus without duplicate shadow',()=>{
  assert.deepEqual(queryEntities(portalData,{...DEFAULTS,query:'杭州市闻涛实验小学（本部）'}).map(e=>e.id),['c']);
  assert.deepEqual(queryEntities(portalData,{...DEFAULTS,query:'闻涛实验小学(本部)',located:'no'}).map(e=>e.id),['s']);
  assert.deepEqual(queryEntities(portalData,{...DEFAULTS,query:'闻涛实验小学(本部)',favoriteOnly:true},['s']).map(e=>e.id),['s']);
  assert.equal(queryEntities(portalData,{...DEFAULTS,year:'2024'}).some(e=>e.id==='s'),true);
  assert.equal(queryEntities(portalData,{...DEFAULTS,query:'闻涛实验小学(东区)'}).length,0);
});
test('city prefixes are search aliases but school qualification still applies',()=>{
  assert.deepEqual(q({query:'杭州市闻涛小学'}),['s']);
  assert.deepEqual(q({query:'闻涛旧名校区'}),[]);
  assert.deepEqual(q({query:'闻涛旧名校区',primaryOnly:false}),['c']);
});
test('unrelated same-name school records are never merged by display name',()=>{
  const sameNames={entities:[{...school,id:'same-a'}, {...school,id:'same-b'}],admissions:[],school_campus_links:[]};
  assert.equal(queryEntities(sameNames,{...DEFAULTS,query:'闻涛小学'}).length,2);
});
test('relationship plan shares exact official objects and honest drawable counts',()=>{
  const plan=relationPlan(data,{admissions:data.admissions},'s',DEFAULTS);
  assert.equal(plan.tier,'official');assert.equal(plan.canScope,true);assert.equal(plan.fallback,false);
  assert.deepEqual(plan.items.map(r=>r.id),['h','u']);
  assert.equal(plan.locatedCount,1);assert.equal(plan.unlocatedCount,1);assert.equal(plan.drawableCount,1);
  const unlocatedSchool=relationPlan(portalData,{admissions:portalData.admissions},'s',DEFAULTS);
  assert.equal(unlocatedSchool.items.length,2);assert.equal(unlocatedSchool.drawableCount,0);
});
test('reverse official relation prefers physical portal campus and avoids affiliation duplicates',()=>{
  const plan=relationPlan(portalData,{admissions:portalData.admissions},'h',DEFAULTS);
  assert.equal(plan.tier,'official');assert.deepEqual(plan.items.map(r=>r.id),['c']);
  assert.deepEqual(plan.items[0].officialSchoolIds,['s']);assert.equal(plan.canScope,false);
});
test('historical campus affiliation is never relabeled a current direct catchment',()=>{
  const plan=relationPlan(portalData,{admissions:portalData.admissions},'old',DEFAULTS);
  assert.equal(plan.tier,'affiliation');assert.equal(plan.fallback,true);assert.equal(plan.canScope,false);
  assert.equal(plan.items[0].evidenceKind,'documented_campus_affiliation');
  const officialSchools=queryEntities(portalData,{...DEFAULTS,officialOnly:true}).map(e=>e.id);
  assert.deepEqual(officialSchools,['c']);
});
test('campus links and district fallback never backfill another admission year',()=>{
  const future=relationPlan(portalData,{admissions:portalData.admissions,nearby:[{entity_id:'h',distance:80}]},'c',{...DEFAULTS,year:'2029'});
  assert.equal(future.tier,'nearby');assert.equal(future.fallback,true);assert.equal(future.hasOfficialYear,false);assert.equal(future.canScope,false);
  assert.deepEqual(future.items.map(r=>r.evidenceKind),['nearby']);
  const noEvidence=relationPlan(portalData,{admissions:portalData.admissions},'c',{...DEFAULTS,year:'2029'});
  assert.equal(noEvidence.tier,'none');assert.deepEqual(noEvidence.items,[]);
});
test('fallback map and list objects share opposite-kind deduplicated evidence',()=>{
  const detail={admissions:[],co_mentions:[{a:'s',b:'h',post_ids:['p1','p2']},{a:'s',b:'h',post_ids:['p2','p3']},{a:'s',b:'c',post_ids:['p4']}],nearby:[{entity_id:'u',distance:100}]};
  const plan=relationPlan(data,detail,'s',DEFAULTS);
  assert.equal(plan.tier,'co');assert.equal(plan.fallback,true);assert.deepEqual(plan.items.map(r=>r.id),['h']);assert.equal(plan.items[0].rows,3);
  const nearby=relationPlan(data,detail,'s',DEFAULTS,'nearby');
  assert.equal(nearby.tier,'nearby');assert.equal(nearby.fallback,false);assert.deepEqual(nearby.items.map(r=>r.id),['u']);
  assert.equal(nearby.locatedCount,0);assert.equal(nearby.unlocatedCount,1);
});
test('official relation plan preserves buildings while deduplicating identical source rows',()=>{
  const a={id:'one',school_id:'s',home_id:'h',year:'2026',admission_type:'户籍生',active:1,payload:{building_number:'1幢'}};
  const plan=relationPlan(data,{admissions:[a,{...a},{...a,id:'two',payload:{building_number:'2幢'}}]},'s',DEFAULTS);
  assert.equal(plan.items.length,1);assert.equal(plan.items[0].rows,2);assert.match(plan.items[0].note,/1幢/);assert.match(plan.items[0].note,/2幢/);
});
test('relationship items obey coupled housing filters on a single price record',()=>{
  const match=relationPlan(data,{admissions:data.admissions},'s',{...DEFAULTS,maxBudget:'310',minArea:'80'});
  assert.equal(match.tier,'official');assert.deepEqual(match.items.map(r=>r.id),['h']);
  const miss=relationPlan(data,{admissions:data.admissions,co_mentions:[{a:'s',b:'h',post_ids:['p']}],nearby:[{entity_id:'h',distance:90}]},'s',{...DEFAULTS,maxBudget:'250',minArea:'80'});
  assert.equal(miss.tier,'none');assert.deepEqual(miss.items,[]);
});
test('official-only schools require an active matching admission type, not directory presence',()=>{
  const types={...portalData,entities:[...portalData.entities,{...school,id:'empty'}],admissions:[
    {school_id:'s',home_id:'h',year:'2026',admission_type:'户籍生',active:1},
    {school_id:'s',home_id:'u',year:'2026',admission_type:'新杭州人',active:0}]};
  assert.deepEqual(queryEntities(types,{...DEFAULTS,officialOnly:true,admission:'户籍生'}).map(e=>e.id),['c']);
  assert.deepEqual(queryEntities(types,{...DEFAULTS,officialOnly:true,admission:'新杭州人'}),[]);
});
test('relationship filters use the selected candidate snapshot',()=>{
  const current=relationPlan(data,{admissions:data.admissions},'s',{...DEFAULTS,candidateOnly:true});
  const historical=relationPlan(data,{admissions:data.admissions},'s',{...DEFAULTS,candidateOnly:true},'official',{snapshot:{}});
  assert.deepEqual(current.items.map(r=>r.id),['h']);assert.deepEqual(historical.items,[]);
});
test('relation grouping preserves same-name homes as distinct source objects',()=>{
  const duplicate={...data,entities:[...data.entities,{...house,id:'h-two'}],admissions:[...data.admissions,{school_id:'s',home_id:'h-two',year:'2026',admission_type:'户籍生',active:1}]};
  const plan=relationPlan(duplicate,null,'s',DEFAULTS);
  assert.deepEqual(plan.items.map(r=>r.id),['h','u','h-two']);
});
test('background home names stay visible while querying schools and housing conditions',()=>{
  const extra={...data,entities:[...data.entities,{...house,id:'across-district',district:'拱墅区'}]};
  const state={...DEFAULTS,kind:'school',query:'闻涛小学',district:'滨江区',year:'2029',admission:'新杭州人',maxBudget:'1',officialOnly:true,favoriteOnly:true,candidateOnly:true,located:'no',rectangle:[0,0,1,1],viewportOnly:true,bounds:[0,0,1,1]};
  assert.deepEqual(contextHomes(extra,state).map(e=>e.id),['h','across-district']);
});
test('background home layer honors every explicit focus and label visibility choice',()=>{
  for(const patch of [{contextHomes:false},{markers:'selected'},{markers:'relations'},{labels:'selected'},{labels:'off'}])assert.deepEqual(contextHomes(data,{...DEFAULTS,...patch}),[]);
  assert.deepEqual(contextHomes(data,{...DEFAULTS,markers:'evidence'}).map(e=>e.id),['h']);
});
test('background home labels exclude foreground duplicates, blank names and missing coordinates',()=>{
  const extra={...data,entities:[...data.entities,{...house,id:'blank',name:' '},{...house,id:'h'},{...house,id:'same-name'}]};
  const drawn=new Set(['h']);
  assert.deepEqual(contextHomes(extra,DEFAULTS,drawn).map(e=>e.id),['same-name']);
  assert.deepEqual([...drawn],['h']);
  assert.deepEqual(contextHomes(data,DEFAULTS).map(e=>e.id),['h']);
});
test('price market uses verified collection or explicit resale URL, not transaction kind',()=>{
  assert.equal(priceMarket({source_id:'deals',kind:'deal'}),'resale');
  assert.equal(priceMarket({source_id:'other',kind:'deal'}),'unknown');
  assert.equal(priceMarket({kind:'listing',payload:{url:'https://hz.ke.com/ershoufang/103140451760.html'}}),'resale');
  assert.equal(priceMarket({kind:'listing',payload:{url:'https://example.com/?next=/ershoufang/123'}}),'unknown');
  assert.equal(priceMarket({kind:'listing',payload:{url:'javascript:/ershoufang/123'}}),'unknown');
});
test('new-project historical references require explicit source wording and reference kind',()=>{
  const oldPrice={kind:'reference',payload:{fit_judgement:'安居客楼盘页仍是历史/新房楼盘口径，当前直连返回验证码；不作为今天二手挂牌参考价'}};
  const reference={kind:'reference',payload:{note:'历史楼盘/新房页口径，仅作公开参考价压力观察'}};
  assert.equal(priceMarket(oldPrice),'new');assert.equal(priceMarket(reference),'new');
  assert.equal(priceMarket({...reference,kind:'deal'}),'unknown');
  assert.equal(priceMarket({kind:'reference',payload:{url:'https://mfang.58.com/hz/xinfang/a',note:'未确认是新房还是二手'}}),'unknown');
  assert.equal(priceMarket(null),'unknown');
  assert.equal(priceMarket({source_id:'new-project:leju:123',kind:'reference'}),'new');
  assert.equal(priceMarket({source_id:'new-project:leju:123',kind:'deal'}),'unknown');
  assert.equal(priceMarket({source_id:'incremental-price:fang:123',kind:'deal'}),'resale');
  assert.equal(priceMarket({source_id:'incremental-price:fang:123',kind:'reference'}),'unknown');
});
test('community profile exposes delivery and reference-image fields from current project payloads',()=>{
  const profile=communityProfile({...house,address:'地图地址'}, {projects:[{source_id:'new-project:p',observed_at:'2026-09-09',payload:{
    detail_observed_at:'2026-09-09T00:00:00+08:00',address:'平台地址',developer:'测试置业',property_type:'住宅',building_types:'高层',
    units_raw:'512户',floor_area_ratio_raw:'2.7',green_ratio_raw:'35%',building_area_raw:'96152㎡',land_area_raw:'35612㎡',
    property_manager:'测试物业',property_rights:'住宅：70年',field_values:{最近交房:'2026年04月30日',物业费:'3.2元/㎡/月',车位:'639个',车位配比:'1:1.24',人车分流:'是',交通情况:'距地铁约400米'},
    prices:[{amount:46000,unit:'元/㎡',price_type:'platform_reference',observed_at:'2026-09-09'}]
  }}]});
  assert.deepEqual(profile.timing,{value:'2026年04月30日',label:'交付时间',basis:'来源页面“最近交房”字段'});
  assert.equal(profile.price.value,46000);assert.equal(profile.price.label,'新房平台参考均价');
  assert.equal(profile.fields.find(item=>item.label==='开发商').value,'测试置业');
  assert.equal(profile.fields.find(item=>item.label==='总车位数').value,'639个');
  assert.deepEqual(profile.surrounding,[{label:'交通',value:'距地铁约400米'}]);
  assert.equal(profile.sourceId,'new-project:p');assert.equal(profile.known,18);assert.equal(profile.total,18);
  assert.equal(profile.missing,0);assert.equal(profile.missingPercent,0);assert.equal(profile.passesCompleteness,true);
});
test('community profile prioritizes dated market reference and labels historical deal fallback honestly',()=>{
  const reference=communityProfile(house,{market_snapshots:[{source_id:'market:s',observed_at:'2026-09-09',payload:{reference_unit_yuan_sqm:82429,source_as_of:'2026-08',metric_semantics:'小区参考均价'}}],prices:[{kind:'deal',unit_yuan_sqm:30000,event_date:'2026-06-01'}]});
  assert.equal(reference.price.value,82429);assert.equal(reference.price.label,'小区参考均价');assert.equal(reference.price.asOf,'2026-08');
  const deals=communityProfile(house,{prices:[{source_id:'deals',kind:'deal',unit_yuan_sqm:28000,event_date:'2026-06-01',observed_at:'2026-06-24'},{source_id:'deals',kind:'deal',unit_yuan_sqm:32000,event_date:'2026-06-20',observed_at:'2026-06-24'}]});
  assert.equal(deals.price.value,30000);assert.equal(deals.price.label,'成交样本均价');assert.equal(deals.price.count,2);
  assert.equal(deals.price.asOf,'2026-06-01 — 2026-06-20');assert.match(deals.price.basis,/不代表当前行情/);
});
test('community profile keeps every key row visible when evidence is missing',()=>{
  const profile=communityProfile({kind:'residential',name:'待补充小区'},{});
  assert.equal(profile.fields.length,16);assert.equal(profile.known,0);assert.equal(profile.total,18);assert.equal(profile.missing,18);assert.equal(profile.missingPercent,100);assert.equal(profile.passesCompleteness,false);assert.equal(profile.timing.value,null);assert.equal(profile.price.value,null);
  assert.equal(profile.fields.every(item=>item.value===null),true);
});
test('observed Fang and Anjuke resale channels are classified without guessing generic URLs',()=>{
  assert.equal(priceMarket({kind:'listing',payload:{url:'https://m.fang.com/esf/hz_xm2010186770/'}}),'resale');
  assert.equal(priceMarket({kind:'listing',payload:{url:'https://mobile.anjuke.com/esf/hz-cm1629880/'}}),'resale');
  assert.equal(priceMarket({kind:'listing',payload:{url:'https://example.com/esf/123/'}}),'unknown');
  assert.equal(priceMarket({kind:'reference',payload:{url:'https://m.fang.com/xiaoqu/hz-123/'}}),'unknown');
});
