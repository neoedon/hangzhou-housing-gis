// The map, result list and counts all consume this one query function.
export const MAIN_DISTRICTS = ['滨江区', '拱墅区', '西湖区', '上城区'];
export const DEFAULTS = Object.freeze({district:'滨江区',kind:'school',query:'',year:'2026',admission:'all',schoolId:'',
  officialOnly:false,candidateOnly:false,favoriteOnly:false,located:'all',primaryOnly:true,
  minBudget:'',maxBudget:'',minArea:'',maxArea:'',builtAfter:'',priceKind:'listing',includeUnknown:false,
  dealFrom:'',dealTo:'',freshOnly:false,viewportOnly:false,bounds:null,rectangle:null,
  labels:'smart',markers:'filtered',contextHomes:true,snapshot:''});
export function normalize(value){return String(value??'').normalize('NFKC').replace(/[\s·•]/g,'').toLowerCase();}
// Source evidence is paged independently of map filters; identities stay exact.
export function sourcePage(sources,{query='',page=1,focus=''}={}){
  const all=Array.isArray(sources)?sources:[],pageSize=40;
  const terms=String(query??'').normalize('NFKC').trim().split(/\s+/).map(normalize).filter(Boolean);
  const matches=focus?all.filter(source=>source.id===focus).slice(0,1):all.filter(source=>{
    if(!terms.length)return true;
    const fields=['label','id','notes','url','source_as_of','observed_at','kind'].map(key=>normalize(source[key]));
    return terms.every(term=>fields.some(field=>field.includes(term)));
  });
  const pageCount=Math.max(1,Math.ceil(matches.length/pageSize));
  const requested=Number(page),current=focus?1:Math.min(pageCount,Math.max(1,Number.isFinite(requested)?Math.floor(requested):1));
  const items=matches.slice((current-1)*pageSize,current*pageSize);
  return {items,total:all.length,matched:matches.length,page:current,pageCount,pageSize,
    start:items.length?(current-1)*pageSize+1:0,end:items.length?(current-1)*pageSize+items.length:0,
    focus,missing:!!focus&&!items.length};
}
export function numeric(value){if(value==null||String(value).trim()==='')return null;const n=Number(String(value).replaceAll(',',''));return Number.isFinite(n)&&n>=0?n:null;}
export function located(e){return Number.isFinite(e?.lat)&&Number.isFinite(e?.lng);}
export function inside(e,b){return located(e)&&!!b&&e.lng>=b[0]&&e.lat>=b[1]&&e.lng<=b[2]&&e.lat<=b[3];}
// Residential names provide geographic context independently of the active research query.
// Explicit focus modes remain literal: no background labels when only selected/related is requested.
export function contextHomes(data,state,drawnIds=[]){
  const s={...DEFAULTS,...state};
  if(!s.contextHomes||['selected','relations'].includes(s.markers)||['selected','off'].includes(s.labels))return [];
  const seen=new Set(drawnIds);
  return (data.entities||[]).filter(e=>{
    if(e.kind!=='residential'||!located(e)||!String(e.name||'').trim()||seen.has(e.id))return false;
    seen.add(e.id);return true;
  });
}
export function rectangle(a,b){return [Math.min(a[0],b[0]),Math.min(a[1],b[1]),Math.max(a[0],b[0]),Math.max(a[1],b[1])];}
export function relationMatches(a,s){return !!a.active&&String(a.year)===String(s.year)&&(s.admission==='all'||a.admission_type===s.admission);}
const sameYear=(a,b)=>String(a)===String(b);
const affiliation=link=>link.kind==='documented_campus_affiliation';
function yearLinks(data,year){return (data.school_campus_links||[]).filter(link=>!year||sameYear(link.year,year));}
export function canonicalSchoolIds(data,id,year){
  const result=new Set([id]);
  for(const link of yearLinks(data,year))if(link.campus_id===id)result.add(link.official_school_id);
  return result;
}
export function campusSchoolIds(data,id,year){
  return [...new Set(yearLinks(data,year).filter(link=>link.official_school_id===id).map(link=>link.campus_id))];
}
export function relatedAdmissions(data,id,s){
  const entity=data.entities.find(e=>e.id===id);
  const schoolIds=entity?.kind==='school'?canonicalSchoolIds(data,id,s.year):new Set([id]);
  return data.admissions.filter(a=>(schoolIds.has(a.school_id)||a.home_id===id)&&relationMatches(a,s));
}
export function isFresh(date,today=new Date().toISOString().slice(0,10),days=30){return !!date&&Date.parse(today)-Date.parse(date)<=days*864e5&&Date.parse(today)>=Date.parse(date);}
export function priceMarket(price){
  if(price?.source_id?.startsWith('new-project:')&&price.kind==='reference')return 'new';
  if(price?.source_id?.startsWith('incremental-price:')&&['deal','listing'].includes(price.kind))return 'resale';
  // The imported "deals" collection traces to 19 archived pages titled 二手房成交.
  if(price?.source_id==='deals')return 'resale';
  const payload=price?.payload||{};
  try{const url=new URL(payload.url);if(['http:','https:'].includes(url.protocol)&&(/\/ershoufang(?:\/|$)/i.test(url.pathname)||(/(^|\.)(fang|anjuke)\.com$/i.test(url.hostname)&&/^\/esf\//i.test(url.pathname))))return 'resale';}catch{}
  // These two source descriptions explicitly identify historical new-project references.
  // A project URL, the word 新房 alone, or a generic "deal" kind is insufficient.
  if(price?.kind==='reference'&&[payload.note,payload.fit_judgement].some(value=>
    /(?:历史[／/]新房楼盘|历史楼盘[／/]新房页)口径/.test(String(value||''))))return 'new';
  return 'unknown';
}

const present=value=>value!==null&&value!==undefined&&String(value).trim()!=='';
const firstPresent=(...values)=>values.find(present)??null;
const profileDate=value=>present(value)?String(value).slice(0,10):null;
const positiveNumber=value=>{const n=Number(value);return Number.isFinite(n)&&n>0?n:null;};
function bestProject(projects=[]){
  const keys=['delivery_date_raw','opening_date_raw','developer','property_type','building_types','units_raw','floor_area_ratio_raw','green_ratio_raw','land_area_raw','building_area_raw','property_manager','management_fee_raw'];
  return [...projects].sort((a,b)=>{
    const pa=a?.payload||{},pb=b?.payload||{},fa=pa.field_values||{},fb=pb.field_values||{};
    const score=p=>keys.filter(key=>present(p[key])).length+['最近交房','开盘时间','最近开盘','物业费','车位','车位配比','人车分流','周边配套','交通情况','小区配套'].filter(key=>present((p.field_values||{})[key])).length;
    return score(pb)-score(pa)||String(b?.observed_at||pb.detail_observed_at||'').localeCompare(String(a?.observed_at||pa.detail_observed_at||''));
  })[0]||null;
}
function averagePrice(rows){
  const values=rows.map(row=>positiveNumber(row?.unit_yuan_sqm)).filter(Boolean);
  return values.length?Math.round(values.reduce((sum,value)=>sum+value,0)/values.length):null;
}
function priceRange(rows){
  const values=rows.map(row=>positiveNumber(row?.unit_yuan_sqm)).filter(Boolean);
  return values.length?{low:Math.min(...values),high:Math.max(...values)}:{low:null,high:null};
}
function newestFirst(rows=[],dateOf=()=>null){
  return [...rows].sort((a,b)=>String(dateOf(b)||'').localeCompare(String(dateOf(a)||'')));
}
function explicitYear(value){
  const match=String(value??'').match(/(?:19|20)\d{2}/);
  return match?Number(match[0]):null;
}
const possibleDuplicate=row=>!!(row?.possible_duplicate||row?.payload?.possible_duplicate);
/** Build one evidence-bounded profile for every residential detail panel. */
export function communityProfile(entity={},detail={}){
  const projectRows=detail.projects||[],projectRow=bestProject(projectRows),project=projectRow?.payload||{},fields=project.field_values||{};
  const projectPayloads=projectRows.map(row=>row?.payload||{});
  const uniqueProjectValue=read=>{
    const values=[];
    for(const payload of projectPayloads){const value=read(payload);if(present(value)&&!values.some(item=>String(item)===String(value)))values.push(value);}
    return values.length===1?values[0]:null;
  };
  const projectValue=key=>firstPresent(project[key],uniqueProjectValue(payload=>payload[key]));
  const fieldValue=(...names)=>firstPresent(...names.map(name=>fields[name]),...names.map(name=>uniqueProjectValue(payload=>(payload.field_values||{})[name])));
  const snapshots=newestFirst((detail.market_snapshots||[]).filter(row=>positiveNumber(row?.payload?.reference_unit_yuan_sqm)),row=>row?.payload?.source_as_of||row?.payload?.observed_at||row?.observed_at);
  const projectPrices=newestFirst((project.prices||[]).filter(price=>!price?.font_encoded&&positiveNumber(price?.amount)&&['元/㎡','元/平方米'].includes(price?.unit)),price=>price?.price_as_of||price?.observed_at||project.detail_observed_at);
  const listingRows=newestFirst((detail.prices||[]).filter(row=>row?.kind==='listing'&&!possibleDuplicate(row)&&positiveNumber(row?.unit_yuan_sqm)),row=>row?.payload?.price_as_of||row?.payload?.source_as_of||row?.observed_at);
  const dealRows=newestFirst((detail.prices||[]).filter(row=>row?.kind==='deal'&&!possibleDuplicate(row)&&positiveNumber(row?.unit_yuan_sqm)),row=>row?.event_date||row?.observed_at);
  let price={value:null,label:'均价',basis:'尚无可可靠展示的均价记录',asOf:null,observedAt:null,count:0,sourceId:null,low:null,high:null};
  if(snapshots.length){
    const row=snapshots[0],payload=row.payload||{};
    const value=positiveNumber(payload.reference_unit_yuan_sqm);
    price={value,label:'小区参考均价',basis:payload.metric_semantics||'平台小区参考值，非逐套成交价',asOf:profileDate(payload.source_as_of),observedAt:profileDate(payload.observed_at||row.observed_at),count:1,sourceId:row.source_id,low:value,high:value};
  }else if(projectPrices.length){
    const item=projectPrices[0];
    const value=positiveNumber(item.amount);
    price={value,label:'新房平台参考均价',basis:'平台参考口径，非备案价或网签成交价',asOf:profileDate(item.price_as_of),observedAt:profileDate(item.observed_at||project.detail_observed_at),count:1,sourceId:projectRow?.source_id||null,low:value,high:value};
  }else if(listingRows.length){
    price={value:averagePrice(listingRows),label:'挂牌样本均价',basis:'已入库挂牌线索均值，非在售核验',asOf:null,observedAt:profileDate(listingRows[0]?.observed_at),count:listingRows.length,sourceId:listingRows[0]?.source_id||null,...priceRange(listingRows)};
  }else if(dealRows.length){
    const dates=dealRows.map(row=>profileDate(row.event_date)).filter(Boolean).sort();
    price={value:averagePrice(dealRows),label:'成交样本均价',basis:'已入库历史成交样本均值，不代表当前行情',asOf:dates.length?`${dates[0]}${dates.at(-1)!==dates[0]?' — '+dates.at(-1):''}`:null,observedAt:profileDate(dealRows[0]?.observed_at),count:dealRows.length,sourceId:dealRows[0]?.source_id||null,...priceRange(dealRows)};
  }
  const delivery=firstPresent(projectValue('delivery_date_raw'),fieldValue('最近交房','交房时间','入住时间'));
  const opening=firstPresent(projectValue('opening_date_raw'),fieldValue('最近开盘','开盘时间','首次开盘'));
  const permits=projectPayloads.flatMap(payload=>payload.presale_permits||[]).map(permit=>profileDate(permit?.permit_date_raw)).filter(Boolean).sort();
  const timingFacts=[
    delivery?{id:'delivery',value:String(delivery),label:'交付时间',basis:'来源页面交房 / 入住字段'}:null,
    opening?{id:'opening',value:String(opening),label:'开盘时间',basis:'来源页面开盘字段'}:null
  ].filter(Boolean);
  const activelySelling=/^(?:待售|在售|预售|现房销售|尾盘)$/.test(String(project.sales_status||''));
  const preferredTiming=activelySelling&&opening?timingFacts.find(item=>item.id==='opening'):timingFacts.find(item=>item.id==='delivery')||timingFacts[0];
  const timing=preferredTiming?{...preferredTiming,related:timingFacts.filter(item=>item.id!==preferredTiming.id)}:
    {value:null,label:'交付 / 开盘',basis:permits.length?`尚未披露；最早预售许可 ${permits[0]}`:'来源尚未披露'};
  const propertyTypeRaw=projectValue('property_type'),propertyType=Array.isArray(propertyTypeRaw)?propertyTypeRaw.filter(Boolean).join('、'):propertyTypeRaw;
  const builtYear=firstPresent(entity?.candidate?.built_year,snapshots[0]?.payload?.built_year_source,fieldValue('建成年代','竣工时间'));
  const observedAt=newestFirst(projectRows,row=>row?.payload?.detail_observed_at||row?.observed_at).map(row=>profileDate(row?.payload?.detail_observed_at||row?.observed_at)).find(Boolean)||null;
  const builtYearNumber=explicitYear(builtYear),ageAsOfYear=explicitYear(observedAt);
  const buildingAge=builtYearNumber&&ageAsOfYear&&ageAsOfYear>=builtYearNumber?`${ageAsOfYear-builtYearNumber}年（截至${ageAsOfYear}）`:null;
  const profileFields=[
    ['basic','楼盘位置',firstPresent(projectValue('address'),entity.address)],['basic','建成年代',builtYear],
    ['basic','物业类型',firstPresent(propertyType,fieldValue('物业类型'))],['basic','建筑类型',firstPresent(projectValue('building_types'),fieldValue('建筑类型'))],['basic','产权年限',firstPresent(projectValue('property_rights'),fieldValue('产权年限'))],
    ['planning','建筑面积',firstPresent(projectValue('building_area_raw'),fieldValue('建筑面积'))],['planning','占地面积',firstPresent(projectValue('land_area_raw'),fieldValue('占地面积'))],
    ['planning','总户数',firstPresent(projectValue('units_raw'),fieldValue('规划户数'))],['planning','容积率',firstPresent(projectValue('floor_area_ratio_raw'),fieldValue('容积率'))],['planning','绿化率',firstPresent(projectValue('green_ratio_raw'),fieldValue('绿化率'))],['planning','开发商',projectValue('developer')],
    ['property','物业公司',firstPresent(projectValue('property_manager'),fieldValue('物业公司'))],['property','物业费用',firstPresent(projectValue('management_fee_raw'),fieldValue('物业费','物业费用'))],
    ['property','总车位数',fieldValue('车位')],['property','车位配比',fieldValue('车位配比')],['property','人车分流',fieldValue('人车分流')]
  ].map(([group,label,value])=>({group,label,value:present(value)?String(value):null}));
  const groups=[['basic','基本情况'],['planning','总体规划'],['property','物业与停车']]
    .map(([id,label])=>({id,label,items:profileFields.filter(item=>item.group===id)}));
  const extendedFields=[
    ['备案 / 来源楼盘名',firstPresent(fieldValue('备案名称','楼盘名称'),projectValue('name'))],['开盘时间',opening],['交付时间',delivery],['房龄参考',buildingAge],
    ['建筑层高',fieldValue('建筑层高','层高情况','层高')],['楼栋总数',fieldValue('楼栋总数','楼栋数')],
    ['楼层状况',fieldValue('楼层状况','楼层情况','楼层')],['户型面积',fieldValue('户型面积','主力户型')],['装修情况',fieldValue('装修情况','装修标准','装修')],
    ['可售车位',fieldValue('可售车位','可售车位数')],['车位价格',fieldValue('车位价格')],['立面材料',fieldValue('立面材料','外立面')],
    ['项目公司',fieldValue('项目公司')],['开发品牌',fieldValue('开发品牌')]
  ].map(([label,value])=>({label,value:present(value)?String(value):null}));
  const surrounding=[['小区设施',fieldValue('小区配套')],['交通',fieldValue('交通情况')],['教育',fieldValue('教育')],['医疗',fieldValue('医疗')],['商业',fieldValue('商业')],['办公',fieldValue('办公')],['景观',fieldValue('景观')],['周边配套',fieldValue('周边配套')]]
    .filter(([,value])=>present(value)).map(([label,value])=>({label,value:String(value)}));
  const known=profileFields.filter(item=>present(item.value)).length+Number(present(timing.value))+Number(positiveNumber(price.value)!==null);
  const total=profileFields.length+2,missing=total-known,missingRate=missing/total;
  const extendedKnown=extendedFields.filter(item=>present(item.value)).length,extendedTotal=extendedFields.length;
  const sourceIds=[...new Set(projectRows.map(row=>row?.source_id).filter(Boolean))];
  return {projectRow,project,projectRows,sourceIds,price,timing,timingFacts,fields:profileFields,groups,extendedFields,extendedKnown,extendedTotal,
    extendedMissing:extendedTotal-extendedKnown,extendedMissingPercent:Math.round((extendedTotal-extendedKnown)/extendedTotal*100),surrounding,
    known,total,missing,missingRate,missingPercent:Math.round(missingRate*100),passesCompleteness:missingRate<0.4,
    observedAt,sourceId:projectRow?.source_id||null};
}
export function priceMatches(p,s,today){
  if(p.kind!==s.priceKind)return false;
  if(s.priceKind==='deal'&&((s.dealFrom&&(!p.event_date||p.event_date<s.dealFrom))||(s.dealTo&&(!p.event_date||p.event_date>s.dealTo))))return false;
  // New observations do not make undated market recommendations into new quotes.
  const quoteDate=p.kind==='deal'?p.event_date:
    p.source_id?.startsWith('incremental-price:')||p.kind==='reference'?
      (p.price_as_of||p.payload?.price_as_of||p.source_as_of||p.payload?.source_as_of):p.observed_at;
  if(s.freshOnly&&!isFresh(quoteDate,today))return false;
  for(const [value,min,max] of [[p.total_wan,s.minBudget,s.maxBudget],[p.area_sqm,s.minArea,s.maxArea]]){
    if(min===''&&max==='')continue;
    const n=numeric(value);if(n===null){if(!s.includeUnknown)return false;}else if((min!==''&&n<Number(min))||(max!==''&&n>Number(max)))return false;
  }
  return true;
}
function housingFiltersActive(s){return ['minBudget','maxBudget','minArea','maxArea','dealFrom','dealTo'].some(k=>s[k]!=='')||s.freshOnly||s.candidateOnly||s.builtAfter!=='';}
function matchesHousing(e,s,snapshot=null,today){
  const candidate=snapshot?snapshot[e.id]:e.candidate;
  if(s.candidateOnly&&!candidate)return false;
  if(s.builtAfter!==''){
    const year=numeric(candidate?.built_year);if(year===null){if(!s.includeUnknown)return false;}else if(year<Number(s.builtAfter))return false;
  }
  const priceActive=['minBudget','maxBudget','minArea','maxArea','dealFrom','dealTo'].some(k=>s[k]!=='')||s.freshOnly;
  if(priceActive){
    const prices=(e.price_filter||[]).filter(p=>p.kind===s.priceKind);
    const allowMissing=s.includeUnknown&&!prices.length&&!s.freshOnly&&!s.dealFrom&&!s.dealTo;
    if(!prices.some(p=>priceMatches(p,s,today))&&!allowMissing)return false;
  }
  return true;
}
function directSchoolIds(data,year){
  const links=yearLinks(data,year),ownRows=new Set((data.admissions||[]).filter(a=>a.active&&sameYear(a.year,year)).map(a=>a.school_id));
  return new Set(data.entities.filter(e=>e.kind==='school'&&((e.official_years||[]).some(y=>sameYear(y,year))||ownRows.has(e.id))&&
    (ownRows.has(e.id)||!links.some(l=>l.campus_id===e.id&&affiliation(l))||links.some(l=>l.campus_id===e.id&&!affiliation(l)))).map(e=>e.id));
}
function admissionSchoolIds(data,s){
  const ids=new Set((data.admissions||[]).filter(a=>relationMatches(a,s)).map(a=>a.school_id));
  for(const link of yearLinks(data,s.year))if(!affiliation(link)&&ids.has(link.official_school_id))ids.add(link.campus_id);
  return ids;
}
// Drop common city prefixes only. Campus, phase and building qualifiers remain searchable.
function searchForms(value){const n=normalize(value);return [n,n.replace(/^杭州市?/,'')];}
function searchMatches(values,query){const queries=searchForms(query).filter(Boolean);return values.some(value=>searchForms(value).some(form=>queries.some(q=>form.includes(q))));}
export function queryEntities(data,s,favorites=[],snapshot=null,today){
  s={...DEFAULTS,...s};
  const favoritesSet=new Set(favorites);
  const officialSchoolIds=admissionSchoolIds(data,s);
  const selectedSchoolIds=s.schoolId?canonicalSchoolIds(data,s.schoolId,s.year):new Set();
  const schoolHomes=new Set(s.schoolId?data.admissions.filter(a=>selectedSchoolIds.has(a.school_id)&&relationMatches(a,s)).map(a=>a.home_id):[]);
  const officialHomeIds=s.officialOnly?new Set(data.admissions.filter(a=>relationMatches(a,s)).map(a=>a.home_id)):null;
  const q=normalize(s.query),byId=new Map(data.entities.map(e=>[e.id,e]));
  const portalLinks=yearLinks(data,s.year).filter(l=>l.kind==='portal_coordinate_match');
  const canonicalNames=new Map();
  for(const link of portalLinks){const e=byId.get(link.official_school_id);if(e)canonicalNames.set(link.campus_id,[...(canonicalNames.get(link.campus_id)||[]),e.name,...(e.aliases||[])]);}
  const housingActive=housingFiltersActive(s);
  const eligibleHomes=housingActive?new Set(data.entities.filter(e=>e.kind==='residential'&&matchesHousing(e,s,snapshot,today)).map(e=>e.id)):null;
  const eligibleSchools=housingActive?admissionSchoolIds({...data,admissions:data.admissions.filter(a=>eligibleHomes.has(a.home_id))},s):null;
  const matches=data.entities.filter(e=>{
    const isGroup=e.kind==='school_group';
    const candidate=snapshot?snapshot[e.id]:e.candidate;
    if(s.district==='main'&&!MAIN_DISTRICTS.includes(e.district))return false;
    if(s.district&&s.district!=='main'&&e.district!==s.district)return false;
    if(s.kind!=='all'&&e.kind!==s.kind)return false;
    if(e.kind==='school'&&s.primaryOnly&&!e.primary_school)return false;
    if(q&&!searchMatches([e.name,...(e.aliases||[]),...(e.project_names||[]),...(canonicalNames.get(e.id)||[]),e.address,candidate?.plate,candidate?.name],q))return false;
    if(s.schoolId&&(isGroup||!schoolHomes.has(e.id)))return false;
    if(s.officialOnly&&!isGroup&&!(e.kind==='school'?officialSchoolIds.has(e.id):officialHomeIds.has(e.id)))return false;
    if(housingActive&&(isGroup||!(e.kind==='school'?eligibleSchools.has(e.id):eligibleHomes.has(e.id))))return false;
    if(s.favoriteOnly&&!favoritesSet.has(e.id))return false;
    const groupAggregate=isGroup&&s.kind==='school_group';
    if(!groupAggregate&&s.located==='yes'&&!located(e))return false;
    if(!groupAggregate&&s.located==='no'&&located(e))return false;
    if(!groupAggregate&&s.viewportOnly&&!inside(e,s.bounds))return false;
    if(!groupAggregate&&s.rectangle&&!inside(e,s.rectangle))return false;
    return true;
  });
  const matchIds=new Set(matches.map(e=>e.id));
  const shadows=new Set(portalLinks.filter(l=>matchIds.has(l.campus_id)&&located(byId.get(l.campus_id))).map(l=>l.official_school_id));
  return matches.filter(e=>located(e)||!shadows.has(e.id)||favoritesSet.has(e.id)).sort((a,b)=>Number(b.kind==='school_group')-Number(a.kind==='school_group')||
     (b.group_member_count||0)-(a.group_member_count||0)||Number(officialSchoolIds.has(b.id))-Number(officialSchoolIds.has(a.id))||
     Number(!!(snapshot?snapshot[b.id]:b.candidate))-Number(!!(snapshot?snapshot[a.id]:a.candidate))||
     (numeric((snapshot?snapshot[a.id]:a.candidate)?.rank)??9999)-(numeric((snapshot?snapshot[b.id]:b.candidate)?.rank)??9999)||
     (b.post_count||0)-(a.post_count||0)||a.name.localeCompare(b.name,'zh'));
}

function median(values){
  const sorted=values.filter(Number.isFinite).sort((a,b)=>a-b);
  if(!sorted.length)return null;
  const middle=Math.floor(sorted.length/2);
  return sorted.length%2?sorted[middle]:Math.round((sorted[middle-1]+sorted[middle])/2);
}
/** Rank residential entities using only deduplicated, dated transaction rows. */
export function districtTransactionRanking(data,state={},metric='count',favorites=[],snapshot=null,today){
  const s={...DEFAULTS,...state,kind:'residential',priceKind:'deal'};
  const eligible=queryEntities(data,s,favorites,snapshot,today);
  const rows=eligible.map(entity=>{
    const deals=(entity.price_filter||[]).filter(row=>row.kind==='deal'&&!row.possible_duplicate&&priceMatches(row,s,today));
    const unitValues=deals.map(row=>positiveNumber(row.unit_yuan_sqm)).filter(Boolean);
    const totalValues=deals.map(row=>positiveNumber(row.total_wan)).filter(Boolean);
    const dates=deals.map(row=>profileDate(row.event_date)).filter(Boolean).sort();
    return {entity,dealCount:deals.length,pricedCount:unitValues.length,totalPricedCount:totalValues.length,
      medianUnit:median(unitValues),totalWan:totalValues.length?Math.round(totalValues.reduce((sum,value)=>sum+value,0)*10)/10:null,
      latestDate:dates.at(-1)||null,earliestDate:dates[0]||null};
  }).filter(row=>row.dealCount>0);
  const value=row=>metric==='unit'?row.medianUnit:metric==='total'?row.totalWan:metric==='latest'?row.latestDate:row.dealCount;
  rows.sort((a,b)=>{
    const av=value(a),bv=value(b);
    if(metric==='latest')return String(bv||'').localeCompare(String(av||''))||b.dealCount-a.dealCount||a.entity.name.localeCompare(b.entity.name,'zh');
    return (Number(bv)||-1)-(Number(av)||-1)||b.dealCount-a.dealCount||String(b.latestDate||'').localeCompare(String(a.latestDate||''))||a.entity.name.localeCompare(b.entity.name,'zh');
  });
  return rows.map((row,index)=>({...row,rank:index+1,metric,value:value(row)}));
}

// A single evidence plan drives relation lists, map highlights and lines.
export function relationPlan(data,detail,selectedId,state,requestedMode='official',{snapshot=null,today}={}){
  const s={...DEFAULTS,...state},byId=new Map(data.entities.map(e=>[e.id,e])),selected=byId.get(selectedId);
  const requested=['official','co','nearby'].includes(requestedMode)?requestedMode:'official';
  const isSchool=selected?.kind==='school',opposite=isSchool?'residential':'school',links=yearLinks(data,s.year);
  const housingActive=housingFiltersActive(s),eligibleHomes=new Set(data.entities.filter(e=>e.kind==='residential'&&(!housingActive||matchesHousing(e,s,snapshot,today))).map(e=>e.id));
  const admissions=detail?.admissions??data.admissions??[];
  const allActive=(data.admissions||[]).filter(a=>relationMatches(a,s));
  const eligibleSchools=admissionSchoolIds({...data,admissions:allActive.filter(a=>eligibleHomes.has(a.home_id))},s);
  const accepts=id=>{const e=byId.get(id);return !!e&&id!==selectedId&&e.kind===opposite&&
    (e.kind!=='school'||!s.primaryOnly||e.primary_school)&&(!housingActive||(isSchool?eligibleHomes.has(id):eligibleSchools.has(id)));};
  const finish=(tier,items)=>{
    const locatedCount=items.filter(r=>r.located).length;
    return {tier,requestedMode:requested,items,fallback:tier!==requested,canScope:isSchool&&tier==='official'&&items.length>0,
      year:String(s.year),locatedCount,unlocatedCount:items.length-locatedCount,drawableCount:located(selected)?locatedCount:0,
      hasOfficialYear:allActive.some(a=>isSchool?canonicalSchoolIds(data,selectedId,s.year).has(a.school_id):a.home_id===selectedId)};
  };
  if(!selected)return finish('none',[]);
  const canonical=canonicalSchoolIds(data,selectedId,s.year),seen=new Set(),official=new Map(),affiliated=new Map();
  const physicalSchool=id=>{
    const candidates=links.filter(l=>l.official_school_id===id&&l.kind==='portal_coordinate_match'&&located(byId.get(l.campus_id)))
      .sort((a,b)=>(a.distance_m??Infinity)-(b.distance_m??Infinity)||a.campus_id.localeCompare(b.campus_id));
    return candidates[0]?.campus_id||id;
  };
  for(const a of admissions){
    if(!relationMatches(a,s)||!(isSchool?canonical.has(a.school_id):a.home_id===selectedId))continue;
    const rowKey=a.id||JSON.stringify([a.school_id,a.home_id,a.year,a.admission_type,a.payload||null]);
    if(seen.has(rowKey))continue;seen.add(rowKey);
    const via=links.filter(l=>l.campus_id===selectedId&&l.official_school_id===a.school_id);
    const isAffiliation=isSchool&&a.school_id!==selectedId&&via.length>0&&via.every(affiliation);
    const id=isSchool?a.home_id:physicalSchool(a.school_id);if(!accepts(id))continue;
    const groups=isAffiliation?affiliated:official;
    const group=groups.get(id)||{id,types:new Set(),notes:new Set(),rows:0,officialSchoolIds:new Set(),evidenceKind:isAffiliation?'documented_campus_affiliation':'official'};
    group.rows++;group.types.add(a.admission_type);group.officialSchoolIds.add(a.school_id);
    for(const note of [a.payload?.building_number,a.payload?.scope_note])if(note)group.notes.add(note);
    if(isAffiliation)group.notes.add('经校区沿革关联；该物理校区招生范围仍需核验');
    else if((isSchool&&a.school_id!==selectedId)||(!isSchool&&id!==a.school_id))group.notes.add('官方编号对应地图点位，地址待核对');
    if(a.payload?.building_number||/(路|街|巷|号|社区)$/.test(a.payload?.residential_name||''))group.notes.add('含地址或社区范围，需核对楼栋');
    groups.set(id,group);
  }
  const groupedItems=groups=>[...groups.values()].map(r=>({id:r.id,note:[...r.types].join(' / ')+(r.notes.size?' · '+[...r.notes].join('；'):''),rows:r.rows,
    located:located(byId.get(r.id)),evidenceKind:r.evidenceKind,officialSchoolIds:[...r.officialSchoolIds]}));
  const co=new Map();
  for(const row of detail?.co_mentions||[]){
    if(row.a!==selectedId&&row.b!==selectedId)continue;
    const id=row.a===selectedId?row.b:row.a;if(!accepts(id))continue;
    const posts=co.get(id)||new Set();for(const post of row.post_ids||[])posts.add(post);co.set(id,posts);
  }
  const near=new Map();
  for(const row of detail?.nearby||[]){
    if(!accepts(row.entity_id)||!Number.isFinite(row.distance)||row.distance<0||row.distance>2000)continue;
    const previous=near.get(row.entity_id);if(!previous||row.distance<previous.distance)near.set(row.entity_id,row);
  }
  const tierItems={
    official:groupedItems(official),affiliation:groupedItems(affiliated),
    co:[...co].filter(([,posts])=>posts.size>0).map(([id,posts])=>({id,note:`${posts.size} 条同帖提及 · 非招生依据`,rows:posts.size,located:located(byId.get(id)),evidenceKind:'co',postIds:[...posts]})),
    nearby:[...near.values()].sort((a,b)=>a.distance-b.distance).map(r=>({id:r.entity_id,note:`直线 ${Math.round(r.distance).toLocaleString('zh-CN')} 米 · 不代表学区`,rows:1,located:located(byId.get(r.entity_id)),evidenceKind:'nearby',distance:r.distance}))
  };
  const order=requested==='official'?['official','affiliation','co','nearby']:requested==='co'?['co','nearby']:['nearby','co'];
  for(const tier of order)if(tierItems[tier].length)return finish(tier,tierItems[tier]);
  const officialHomes=new Set(allActive.map(a=>a.home_id)),officialSchools=directSchoolIds(data,s.year),districtSeen=new Set();
  const districtItems=data.entities.filter(e=>e.district===selected.district&&(isSchool?officialHomes.has(e.id):officialSchools.has(e.id)))
    .map(e=>isSchool?e:byId.get(physicalSchool(e.id))).filter(e=>e&&accepts(e.id)&&!districtSeen.has(e.id)&&districtSeen.add(e.id))
    .sort((a,b)=>Number(located(b))-Number(located(a))||(b.post_count||0)-(a.post_count||0)||a.name.localeCompare(b.name,'zh')).slice(0,8)
    .map(e=>({id:e.id,note:`${s.year} 年同区官方资料入口 · 尚非该地点对口结论`,rows:0,located:located(e),evidenceKind:'district'}));
  return finish(districtItems.length?'district':'none',districtItems);
}
export function toggleCompare(ids,id){if(ids.includes(id))return ids.filter(x=>x!==id);if(ids.length>=4)return ids;return [...ids,id];}
// Keep zoom expressions at the top level as required by MapLibre; clamp outputs, not zoom stops.
export function minimumTextSize(value,min=12){
  if(typeof value==='number')return Math.max(min,value);
  if(value==null)return min;
  if(Array.isArray(value)&&['interpolate','interpolate-hcl','interpolate-lab'].includes(value[0]))return value.map((x,i)=>i>=4&&i%2===0?minimumTextSize(x,min):x);
  if(Array.isArray(value)&&value[0]==='step')return value.map((x,i)=>i>=2&&i%2===0?minimumTextSize(x,min):x);
  return ['max',min,value];
}
export function readSaved(raw,validIds){try{const x=JSON.parse(raw);return Array.isArray(x)?[...new Set(x.filter(id=>typeof id==='string'&&validIds.has(id)))]:[];}catch{return [];}}
export function fundingGap({buy,sale,cash,cost,debt}){
  const values=[buy,sale,cash,cost,debt].map(numeric);if(values.some(n=>n===null))return null;
  const [b,s,c,f,d]=values;return {netSale:s-d,available:s-d+c,gap:b+f-(s-d+c)};
}
export function boundsOf(entities){const p=entities.filter(located);if(!p.length)return null;return [Math.min(...p.map(x=>x.lng)),Math.min(...p.map(x=>x.lat)),Math.max(...p.map(x=>x.lng)),Math.max(...p.map(x=>x.lat))];}
// Archived policy visibility is independent of the selected admission year.
// It never contributes to relatedAdmissions, official filters or map lines.
export function policyGroups(records,year){
  return {
    current:records.filter(p=>p.year===year),
    otherArchives:records.filter(p=>p.kind==='local_policy_archive'&&/^\d{4}$/.test(p.year||'')&&p.year!==year)
  };
}
export function filterPosts(posts,data,s,favorites=[],snapshot=null){
  const scopeSet=new Set(queryEntities(data,{...s,query:''},favorites,snapshot).map(e=>e.id));
  const housingActive=['minBudget','maxBudget','minArea','maxArea','dealFrom','dealTo','builtAfter'].some(k=>s[k]!=='')||s.freshOnly;
  const allowUnlocated=s.kind==='all'&&!s.schoolId&&!s.viewportOnly&&!s.rectangle&&!s.officialOnly&&!s.candidateOnly&&!s.favoriteOnly&&s.located==='all'&&!housingActive;
  return posts.filter(p=>p.place_ids.some(id=>scopeSet.has(id))||(!p.place_ids.length&&allowUnlocated&&(!s.district||s.district==='main'&&MAIN_DISTRICTS.some(d=>normalize(p.district).includes(d.replace('区','')))||s.district!=='main'&&normalize(p.district).includes(s.district.replace('区','')))));
}
