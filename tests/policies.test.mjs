import {test} from 'node:test';
import assert from 'node:assert/strict';
import {policyGroups,relatedAdmissions,DEFAULTS} from '../web/model.js';

test('local policy archives remain visible separately from the selected year',()=>{
  const a={id:'old',year:'2025',kind:'local_policy_archive'};
  const b={id:'new',year:'2026',kind:'local_policy_archive'};
  const c={id:'scope',year:'2026',kind:'school_scope'};
  assert.deepEqual(policyGroups([a,b,c],'2026'),{current:[b,c],otherArchives:[a]});
  assert.deepEqual(policyGroups([a,b],'2024'),{current:[],otherArchives:[a,b]});
});
test('unknown years and legacy off-year records are not relabeled as local archives',()=>{
  const records=[{year:'unknown',kind:'local_policy_archive'},{year:'2025',kind:'timeline'}];
  assert.deepEqual(policyGroups(records,'2026'),{current:[],otherArchives:[]});
});
test('archived school and home mentions never become official admissions',()=>{
  const records=[{year:'2025',kind:'local_policy_archive',school_ids:['s','h']}];
  const before=JSON.stringify(records);
  policyGroups(records,'2026');
  assert.equal(JSON.stringify(records),before);
  assert.deepEqual(relatedAdmissions({entities:[{id:'s',kind:'school'}],admissions:[],school_campus_links:[],policies:records},'s',DEFAULTS),[]);
});
