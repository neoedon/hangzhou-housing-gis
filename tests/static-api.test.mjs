import {test} from 'node:test';
import assert from 'node:assert/strict';
import {entityShard,searchStaticPosts,staticDataEnabled} from '../web/static-api.js';

test('static data mode requires the generated pages marker',()=>{
  assert.equal(staticDataEnabled({querySelector:()=>({content:'true'})}),true);
  assert.equal(staticDataEnabled({querySelector:()=>({content:'false'})}),false);
  assert.equal(staticDataEnabled({querySelector:()=>null}),false);
});

test('entity shard is stable, bounded and sensitive to full id',()=>{
  assert.equal(entityShard('osm:way:430812030'),'104');
  assert.equal(entityShard('official:school:2133001518001'),'043');
  assert.notEqual(entityShard('osm:way:430812030'),entityShard('osm:way:430812031'));
  for(const id of ['official:school:2133001518001','local:residential:69854a67d8c705518265']){
    const shard=Number(entityShard(id));assert.ok(shard>=0&&shard<128);
  }
});

test('static post search matches title or body literally and preserves server result shape',()=>{
  const posts=[
    {id:'1',title:'滨江学区',description:'正文'},
    {id:'2',title:'拱墅二手房',description:'含 100% 与下划线_字符'},
    {id:'3',title:'其他',description:null},
  ];
  assert.deepEqual(searchStaticPosts(posts,' 滨江 ').posts.map(post=>post.id),['1']);
  assert.deepEqual(searchStaticPosts(posts,'%').posts.map(post=>post.id),['2']);
  assert.equal(searchStaticPosts(posts,'').total,3);
  assert.deepEqual(Object.keys(searchStaticPosts(posts,'学区')).sort(),['limit','posts','total']);
});
