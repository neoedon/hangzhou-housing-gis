const cache=new Map();

export function staticDataEnabled(doc=globalThis.document){
  return doc?.querySelector?.('meta[name="gis-static-data"]')?.content==='true';
}

// FNV-1a over ASCII entity IDs. Entity IDs are validated as ASCII during export.
export function entityShard(id,count=128){
  let hash=0x811c9dc5;
  for(let index=0;index<String(id).length;index++){
    hash^=String(id).charCodeAt(index);
    hash=Math.imul(hash,0x01000193)>>>0;
  }
  return String(hash%count).padStart(3,'0');
}

export function searchStaticPosts(posts,query,limit=5000){
  const term=String(query??'').trim().toLocaleLowerCase('zh-CN');
  const matched=posts.filter(post=>[post.title,post.description].some(value=>String(value??'').toLocaleLowerCase('zh-CN').includes(term)));
  return {total:matched.length,limit,posts:matched.slice(0,limit)};
}

async function readJson(path){
  if(!cache.has(path))cache.set(path,fetch(new URL(path,import.meta.url),{cache:'no-cache'}).then(async response=>{
    if(!response.ok)throw Error(`静态资料读取失败（${response.status}）`);
    return response.json();
  }).catch(error=>{cache.delete(path);throw error;}));
  return cache.get(path);
}

function missing(message){throw Error(message);}

export async function staticGet(input){
  const url=new URL(input,'https://static.invalid');
  if(url.pathname==='/api/bootstrap')return readJson('./data/bootstrap.json');
  const manifest=await readJson('./data/manifest.json');
  if(url.pathname==='/api/entity'){
    const id=url.searchParams.get('id')||'';
    const shard=await readJson(`./data/entities/${entityShard(id,manifest.entity_shards)}.json`);
    return shard[id]||missing('没有找到这个地点的静态详情');
  }
  if(url.pathname==='/api/posts'){
    const posts=await readJson('./data/posts.json');
    return searchStaticPosts(posts,url.searchParams.get('q')||'');
  }
  if(url.pathname==='/api/snapshot'){
    const date=url.searchParams.get('date')||'';
    const snapshots=await readJson('./data/snapshots.json');
    return snapshots[date]||{date,records:[]};
  }
  if(url.pathname==='/api/source'){
    const sources=await readJson('./data/sources.json'),id=url.searchParams.get('id')||'';
    return sources[id]||missing('没有找到这条静态来源');
  }
  if(url.pathname==='/api/source-archive'){
    const archives=await readJson('./data/archives.json'),id=url.searchParams.get('id')||'';
    return archives[id]||missing('这条来源没有可公开读取的本地副本');
  }
  throw Error('静态站点不支持这个资料接口');
}
