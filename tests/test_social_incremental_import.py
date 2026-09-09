import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from build_data import Builder, SCHEMA, digest, dump
from social_incremental_import import body_hash, meaningful_characters, exact_place_mentions, integrate_social, new_post_district


def note_id(number):
    return f'{number:024x}'


class SocialIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript(SCHEMA)
        self.builder = Builder(self.db)
        self.temp = tempfile.TemporaryDirectory(prefix='social-import-test-',dir=APP/'data/incremental')
        self.folder = Path(self.temp.name)
        self.baseline_path = self.folder/'baseline.json'
        self.bundle = self.folder/'reviewed-social.json'
        self.old_id, self.body_id = note_id(1), note_id(2)
        self.baseline_body = '原始已采集的杭州小区正文保留不覆盖，需要核对实际位置交通物业和学校政策，网站帖子不能作为招生依据。'
        self.baseline = dict(frozen_at='2026-09-09T00:00:00+08:00',valid_ids=[self.old_id,self.body_id],
                             body_ids=[self.body_id],body_hashes=[body_hash(self.baseline_body)],valid_count=2,body_count=1)
        self.baseline_path.write_text(dump(self.baseline))
        self.baseline_sha = digest(self.baseline_path.read_bytes())
        self.db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?)',
                        (self.old_id,'原有索引标题','','search_only','2026-08-01','2026-09-07','https://www.xiaohongshu.com/explore/'+self.old_id,'拱墅区','["原有主题"]'))
        self.db.execute('INSERT INTO posts VALUES(?,?,?,?,?,?,?,?,?)',
                        (self.body_id,'原有正文',self.baseline_body,'detail_description','2026-08-01','2026-09-07','https://www.xiaohongshu.com/explore/'+self.body_id,'滨江区','[]'))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def row(self, pid=None, description=None, **changes):
        pid = pid or note_id(3)
        description = description or '杭州滨江区彩虹城和杭州市彩虹小学周边的居住体验，交通步行路线和小区物业管理各有差异，这是个人看房观察，不代表招生或成交结论。'
        row = dict(id=pid,source_url='https://www.xiaohongshu.com/explore/'+pid,observed_at='2026-09-09T01:00:00+08:00',
                   visible_title='杭州居住观察',visible_description=description,
                   record=dict(id=pid,title='杭州居住观察',desc=description,display_date='2026-09-08',
                               description_kind='prose_or_caption',valid=True,exclusion_reasons=[],district_hints=['滨江'],keywords=['小区']),
                   body_sha256=body_hash(description),meaningful_characters=meaningful_characters(description),
                   accepted=True,exclusion_reason=None,baseline_index=pid in self.baseline['valid_ids'])
        row.update(changes)
        return row

    def run_import(self, rows, **kwargs):
        self.bundle.write_text(dump(rows))
        accepted = self.builder.read(self.bundle)
        return integrate_social(self.builder,self.bundle,accepted,baseline_path=self.baseline_path,
                                baseline_sha256=kwargs.pop('baseline_sha256',self.baseline_sha),**kwargs)

    def test_existing_index_gains_only_body_and_depth_not_another_post(self):
        original = self.db.execute('SELECT title,posted_date,url,district,topics FROM posts WHERE id=?',(self.old_id,)).fetchone()
        row=self.row(self.old_id);result=self.run_import([row])
        self.assertEqual((result['social_new_posts'],result['social_new_bodies'],result['social_existing_index_deep_reads']),(0,1,1))
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0],2)
        self.assertEqual(self.db.execute('SELECT title,posted_date,url,district,topics FROM posts WHERE id=?',(self.old_id,)).fetchone(),original)
        self.assertEqual(self.db.execute('SELECT description,depth FROM posts WHERE id=?',(self.old_id,)).fetchone(),(row['record']['desc'],'detail_description'))

    def test_new_id_adds_post_body_and_provenance(self):
        row=self.row();result=self.run_import([row])
        self.assertEqual((result['social_new_posts'],result['social_new_bodies']),(1,1))
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0],3)
        source=self.db.execute('SELECT kind,sha256,url,notes FROM sources').fetchone()
        self.assertEqual(source[:3],('social_evidence',digest(self.bundle.read_bytes()),row['source_url']))
        self.assertIn(row['body_sha256'],source[3])
        self.assertIn(self.baseline_sha,source[3])
        self.assertEqual(self.builder.hashes[str(self.baseline_path.relative_to(APP.parent))],self.baseline_sha)

    def test_baseline_body_id_and_hash_are_excluded_without_overwrite(self):
        rows=[self.row(self.body_id),self.row(note_id(4),self.baseline_body)]
        result=self.run_import(rows)
        self.assertEqual(result['social_new_bodies'],0)
        self.assertEqual(result['social_exclusion_counts'],{'baseline_existing_body':1,'duplicate_body_baseline':1})
        self.assertEqual(self.db.execute('SELECT description FROM posts WHERE id=?',(self.body_id,)).fetchone()[0],self.baseline_body)

    def test_exact_note_id_and_source_url_must_both_match(self):
        mismatch=self.row();mismatch['record']['id']=note_id(9)
        wrong_url=self.row(note_id(4));wrong_url['source_url']='https://www.xiaohongshu.com/explore/'+note_id(9)
        foreign=self.row(note_id(5));foreign['source_url']='https://example.com/explore/'+note_id(5)
        result=self.run_import([mismatch,wrong_url,foreign])
        self.assertEqual(result['social_new_bodies'],0)
        self.assertEqual(result['social_exclusion_counts'],{'exact_note_id_mismatch':1,'exact_source_url_mismatch':2})

    def test_short_empty_tags_and_out_of_scope_are_distinct_exclusions(self):
        short=self.row(description='杭州彩虹城短文',accepted=False,exclusion_reason='short_or_low_content')
        empty=self.row(note_id(4));empty['record']['desc']=''
        tags=self.row(note_id(5),description='#杭州买房[话题]# [点赞R]')
        invalid=self.row(note_id(6));invalid['record']['valid']=False
        not_accepted=self.row(note_id(7),accepted=False,exclusion_reason='out_of_scope')
        result=self.run_import([short,empty,tags,invalid,not_accepted])
        self.assertEqual(result['social_exclusion_counts'],{'short_or_low_content':1,'no_prose':2,'out_of_scope':2})
        self.assertEqual(result['social_new_bodies'],0)

    def test_rental_only_is_not_imported_even_if_mislabeled_valid(self):
        row=self.row(description='杭州滨江区彩虹城整租房源，租期一年家具齐全可拎包入住，交通方便生活配套完善，欢迎有需要租房的朋友预约看房了解租赁情况。')
        self.assertEqual(self.run_import([row])['social_exclusion_counts'],{'rental_only':1})

    def test_claimed_body_hash_and_meaningful_count_are_recomputed(self):
        altered=self.row();altered['body_sha256']='0'*64
        bad_count=self.row(note_id(4));bad_count['meaningful_characters']=10000
        result=self.run_import([altered,bad_count])
        self.assertEqual(result['social_exclusion_counts'],{'body_hash_mismatch':1,'meaningful_character_count_mismatch':1})

    def test_duplicate_note_and_normalized_body_in_bundle_count_once(self):
        row=self.row();other=self.row(note_id(4),description=row['record']['desc']+' #附加标签[话题]# [赞R]')
        result=self.run_import([row,copy.deepcopy(row),other])
        self.assertEqual(result['social_new_bodies'],1)
        self.assertEqual(result['social_exclusion_counts'],{'duplicate_note_id_in_bundle':1,'duplicate_body_in_bundle':1})

    def test_second_import_is_idempotent_and_does_not_rewrite_body(self):
        rows=[self.row(self.old_id),self.row(note_id(4),description='杭州拱墅区小区交通配套与生活设施的独立观察，最近看房感受到不同楼栋采光和物业维护差异，具体房屋情况还需要实际核验。')]
        self.assertEqual(self.run_import(rows)['social_new_bodies'],2)
        self.assertEqual(self.run_import(rows)['social_new_bodies'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0],3)

    def test_literal_body_mentions_add_only_school_home_co_mentions(self):
        self.builder.entity('osm:home','彩虹城','residential','滨江区',30.19,120.18)
        self.builder.entity('osm:school','杭州市彩虹小学','school','滨江区',30.20,120.19)
        self.builder.entity('osm:unmentioned','滨江实验小学','school','滨江区',30.21,120.20)
        result=self.run_import([self.row()])
        self.assertEqual(result['social_new_mentions'],2)
        self.assertEqual(set(r[0] for r in self.db.execute('SELECT entity_id FROM post_places')),{'osm:home','osm:school'})
        pair=self.db.execute('SELECT a,b,post_ids,distance FROM co_mentions').fetchone()
        self.assertEqual(pair,('osm:home','osm:school',dump([note_id(3)]),None))
        self.assertEqual((result['social_new_co_mentions'],result['social_new_co_mention_evidence']),(1,1))
        for table in ('admissions','school_campus_links','prices'):
            self.assertEqual(self.db.execute('SELECT count(*) FROM '+table).fetchone()[0],0)

    def test_existing_pair_unions_post_ids_keeps_distance_and_is_idempotent(self):
        self.builder.entity('osm:home','彩虹城','residential','滨江区',30.19,120.18)
        self.builder.entity('osm:school','杭州市彩虹小学','school','滨江区',30.20,120.19)
        self.db.execute('INSERT INTO co_mentions VALUES(?,?,?,?)',
                        ('osm:home','osm:school',dump([self.body_id,self.body_id]),123.456))
        self.db.execute('INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?)',
                        ('official-test','osm:school','osm:home',None,'2026','户籍生',1,'{"original":true}'))
        admission_before=self.db.execute('SELECT * FROM admissions').fetchall()
        entities_before=self.db.execute('SELECT * FROM entities').fetchall()
        first=self.row(self.old_id)
        second=self.row(note_id(4),description=first['record']['desc']+'另外补充小区采光观察。')
        result=self.run_import([first,second])
        self.assertEqual((result['social_new_co_mentions'],result['social_new_co_mention_evidence']),(0,2))
        expected=('osm:home','osm:school',dump(sorted([self.body_id,self.old_id,note_id(4)])),123.456)
        self.assertEqual(self.db.execute('SELECT * FROM co_mentions').fetchone(),expected)
        again=self.run_import([first,second])
        self.assertEqual((again['social_new_co_mentions'],again['social_new_co_mention_evidence']),(0,0))
        self.assertEqual(self.db.execute('SELECT * FROM co_mentions').fetchone(),expected)
        self.assertEqual(self.db.execute('SELECT * FROM admissions').fetchall(),admission_before)
        self.assertEqual(self.db.execute('SELECT * FROM entities').fetchall(),entities_before)

    def test_multiple_body_mentions_pair_only_opposite_kinds(self):
        for eid,name,kind in [('h1','彩虹城','residential'),('h2','阳光花园','residential'),
                              ('s1','杭州市彩虹小学','school'),('s2','滨江实验小学','school')]:
            self.builder.entity(eid,name,kind,'滨江区')
        row=self.row(description='杭州彩虹城、阳光花园、杭州市彩虹小学和滨江实验小学的个人走访观察，关注交通步行和社区维护，不代表这些地点之间存在招生对口关系。')
        result=self.run_import([row])
        pairs=self.db.execute('SELECT a,b,post_ids,distance FROM co_mentions').fetchall()
        self.assertEqual({(a,b) for a,b,_,_ in pairs},{('h1','s1'),('h1','s2'),('h2','s1'),('h2','s2')})
        self.assertEqual((result['social_new_co_mentions'],result['social_new_co_mention_evidence']),(4,4))
        self.assertTrue(all(json.loads(ids)==[row['id']] and distance is None for _,_,ids,distance in pairs))

    def test_title_tags_and_existing_post_places_do_not_supply_body_pair(self):
        self.builder.entity('home','彩虹城','residential','滨江区')
        self.builder.entity('school','杭州市彩虹小学','school','滨江区')
        self.db.execute('INSERT INTO post_places VALUES(?,?)',(self.old_id,'home'))
        description='杭州杭州市彩虹小学周边交通和步行路线的个人观察，具体学校的校园入口以及办学情况应当结合正式公开材料核实，这篇文章不代表招生或成交结论。'
        row=self.row(self.old_id,description=description+' #彩虹城[话题]#')
        row['record']['title']='彩虹城周边观察'
        result=self.run_import([row])
        self.assertEqual(result['social_new_bodies'],1)
        self.assertEqual(set(r[0] for r in self.db.execute('SELECT entity_id FROM post_places')),{'home','school'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM co_mentions').fetchone()[0],0)

    def test_alias_only_and_ambiguous_full_names_do_not_form_pairs(self):
        self.builder.entity('home','彩虹花园','residential','滨江区',aliases=['彩虹城'])
        self.builder.entity('school','杭州市彩虹小学','school','滨江区')
        result=self.run_import([self.row()])
        self.assertEqual(result['social_new_mentions'],2)
        self.assertEqual(result['social_new_co_mentions'],0)
        self.builder.entity('duplicate-home','彩虹花园','residential','拱墅区')
        row=self.row(note_id(4),description='杭州彩虹花园和杭州市彩虹小学的个人观察，涉及社区环境以及日常出行路线，但缺少具体区属和门牌说明，不能依据同名自动选定住宅或建立招生关系。')
        result=self.run_import([row])
        self.assertEqual(result['social_new_co_mentions'],0)
        self.assertIn('彩虹花园',result['social_ambiguous_mentions'][0]['names'])

    def test_longest_campus_name_does_not_create_parent_school_pair(self):
        self.builder.entity('home','彩虹城','residential','滨江区')
        self.builder.entity('parent','学军小学','school','西湖区')
        self.builder.entity('campus','学军小学紫金港校区','school','西湖区')
        row=self.row(description='杭州彩虹城和学军小学紫金港校区是这次分别走访的两个地点，记录社区维护与学校周边交通体验，虽然文章同时提及，但不能因此认定它们存在招生对口关系。')
        result=self.run_import([row])
        self.assertEqual(result['social_new_co_mentions'],1)
        self.assertEqual(self.db.execute('SELECT a,b FROM co_mentions').fetchall(),[('campus','home')])

    def test_roads_address_labels_and_generic_housing_are_not_new_home_edges(self):
        self.builder.entity('school','杭州市彩虹小学','school','滨江区')
        for eid,name in [('home','彩虹城'),('road','石祥路'),('address','金昌路66号'),('generic','安置房')]:
            self.builder.entity(eid,name,'residential','滨江区')
        old=('road','school',dump([self.body_id]),456.7)
        self.db.execute('INSERT INTO co_mentions VALUES(?,?,?,?)',old)
        row=self.row(description='杭州市彩虹小学和彩虹城的社区走访观察，同时提到石祥路、金昌路66号和安置房等道路或泛称，不能把这些地址标签直接当作具名住宅小区，更不能作为招生依据。')
        result=self.run_import([row])
        self.assertEqual(result['social_new_co_mentions'],1)
        self.assertEqual(self.db.execute('SELECT * FROM co_mentions WHERE a=?',('road',)).fetchone(),old)
        self.assertEqual({(a,b) for a,b in self.db.execute('SELECT a,b FROM co_mentions')},{('home','school'),('road','school')})
        self.assertEqual(set(result['social_excluded_co_mention_places'][0]['names']),{'石祥路','金昌路66号','安置房'})
        self.assertEqual(self.db.execute('SELECT count(*) FROM post_places').fetchone()[0],5)

    def test_rejected_or_preexisting_bodies_do_not_add_co_mentions(self):
        self.builder.entity('home','彩虹城','residential','滨江区')
        self.builder.entity('school','杭州市彩虹小学','school','滨江区')
        rows=[self.row(accepted=False,exclusion_reason='not_reviewed'),self.row(self.body_id)]
        result=self.run_import(rows)
        self.assertEqual(result['social_new_bodies'],0)
        self.assertEqual((result['social_new_co_mentions'],result['social_new_co_mention_evidence']),(0,0))
        self.assertEqual(self.db.execute('SELECT count(*) FROM co_mentions').fetchone()[0],0)

    def test_invalid_existing_co_mention_evidence_is_not_overwritten(self):
        self.builder.entity('home','彩虹城','residential','滨江区')
        self.builder.entity('school','杭州市彩虹小学','school','滨江区')
        original=('home','school','{"unexpected":"object"}',85.0)
        self.db.execute('INSERT INTO co_mentions VALUES(?,?,?,?)',original)
        with self.assertRaisesRegex(ValueError,'Invalid existing co-mention post_ids'):
            self.run_import([self.row()])
        self.assertEqual(self.db.execute('SELECT * FROM co_mentions').fetchone(),original)

    def test_longest_campus_term_and_ambiguous_names_are_not_fuzzy_mapped(self):
        names={'学军小学':{'school-parent'},'学军小学紫金港校区':{'school-campus'},'实验小学':{'school-a','school-b'}}
        mapped,ambiguous=exact_place_mentions('今天走访学军小学紫金港校区，另外提到实验小学。',names)
        self.assertEqual(mapped,{'school-campus'})
        self.assertEqual(ambiguous,['实验小学'])

    def test_missing_baseline_hash_changed_baseline_or_ungated_bundle_blocks_all(self):
        row=self.row()
        with self.assertRaisesRegex(ValueError,'baseline_sha256'):
            self.run_import([row],baseline_sha256=None)
        with self.assertRaisesRegex(ValueError,'baseline changed'):
            self.run_import([row],baseline_sha256='0'*64)
        self.bundle.write_text(dump([row]))
        self.builder.hashes.clear()
        with self.assertRaisesRegex(ValueError,'manifest hash gate'):
            integrate_social(self.builder,self.bundle,[row],baseline_sha256=self.baseline_sha,baseline_path=self.baseline_path)
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0],2)

    def test_baseline_index_claim_is_rechecked_and_original_index_required(self):
        self.assertEqual(self.run_import([self.row(self.old_id,baseline_index=False)])['social_exclusion_counts'],{'baseline_index_mismatch':1})
        self.db.execute('DELETE FROM posts WHERE id=?',(self.old_id,))
        with self.assertRaisesRegex(ValueError,'original frozen post index'):
            self.run_import([self.row()])

    def test_observation_timestamp_is_not_replaced_by_post_date(self):
        row=self.row();self.run_import([row])
        self.assertEqual(self.db.execute('SELECT posted_date,observed_at FROM posts WHERE id=?',(row['id'],)).fetchone(),
                         ('2026-09-08','2026-09-09T01:00:00+08:00'))

    def test_hash_rules_match_punctuation_tags_width_emoji_and_astral_length(self):
        self.assertEqual(body_hash('ＡＢＣ 杭州！#房产[话题]# [点赞R]'),body_hash('abc杭州'))
        self.assertEqual(meaningful_characters('𐐀'),2)
        self.assertEqual(meaningful_characters('#房产[话题]# [点赞R]'),0)

    def reviewed_rows(self, rows, decisions=None, **document_fields):
        path=self.folder/'semantic-review.json'
        document=dict(schema_version=1,status='sealed_for_root_review',records=decisions if decisions is not None else
                      [dict(id=r['id'],decision='accept',body_sha256=r['body_sha256'],body_read_complete=True,
                            count_toward_unique_in_scope_bodies=True) for r in rows])
        document.update(document_fields)
        path.write_text(dump(document))
        expected=digest(path.read_bytes())
        for row in rows:
            row['semantic_review']=dict(decision='accept',body_sha256=row['body_sha256'],
                review_source=str(path.relative_to(APP.parent)),review_sha256=expected)
        return path

    def test_required_semantic_review_reads_and_registers_real_source_once(self):
        first=self.row(self.old_id)
        second=self.row(note_id(4),description=first['record']['desc']+'本条另外讨论楼栋采光。')
        path=self.reviewed_rows([first,second])
        with patch.object(self.builder,'read',wraps=self.builder.read) as read:
            result=self.run_import([first,second],require_semantic_review=True)
        self.assertEqual((result['social_new_posts'],result['social_new_bodies']),(1,2))
        self.assertEqual(sum(Path(call.args[0])==path for call in read.call_args_list),1)
        self.assertEqual(self.builder.hashes[str(path.relative_to(APP.parent))],digest(path.read_bytes()))
        self.assertTrue(all('semantic_review_sha256=' in r[0] for r in self.db.execute('SELECT notes FROM sources')))

    def test_required_review_rejects_missing_or_self_declared_acceptance(self):
        row=self.row()
        self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                         {'semantic_review_acceptance_required':1})
        row['semantic_review']=dict(decision='accept',body_sha256=row['body_sha256'])
        self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                         {'semantic_review_file_hash_required':1})
        self.reviewed_rows([row]);row['semantic_review']['body_sha256']='0'*64
        self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                         {'semantic_review_claim_body_hash_mismatch':1})
        self.assertEqual(self.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_source_decision_cannot_be_overridden_by_row_accept(self):
        for verdict in ('exclude','review'):
            row=self.row()
            self.reviewed_rows([row],[dict(id=row['id'],decision=verdict,body_sha256=row['body_sha256'])])
            self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                             {'semantic_review_source_does_not_accept':1})
        self.assertEqual(self.db.execute('SELECT count(*) FROM posts').fetchone()[0],2)

    def test_wrong_review_id_body_hash_and_duplicate_decisions_are_rejected(self):
        cases=[([dict(id=note_id(9),decision='accept',body_sha256=self.row()['body_sha256'])],'semantic_review_note_not_found'),
               ([dict(id=note_id(3),decision='accept',body_sha256='0'*64)],'semantic_review_source_body_hash_mismatch')]
        decision=dict(id=note_id(3),decision='accept',body_sha256=self.row()['body_sha256'])
        cases.append(([decision,copy.deepcopy(decision)],'semantic_review_duplicate_decision'))
        for decisions,reason in cases:
            row=self.row();self.reviewed_rows([row],decisions)
            self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],{reason:1})

    def test_missing_changed_outside_and_unreviewed_files_are_rejected(self):
        row=self.row();path=self.reviewed_rows([row])
        path.write_text(dump(dict(records=[])))
        self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                         {'semantic_review_file_changed_or_hash_mismatch':1})
        for source in ('../outside-semantic-review.json',str(self.folder/'missing.json')):
            row=self.row();self.reviewed_rows([row]);row['semantic_review']['review_source']=source
            self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                             {'semantic_review_source_missing_or_outside':1})
        row=self.row();self.reviewed_rows([row],reviewed=False)
        self.assertEqual(self.run_import([row],require_semantic_review=True)['social_exclusion_counts'],
                         {'semantic_review_file_unreviewed':1})

    def test_changed_review_during_builder_read_fails_closed(self):
        row=self.row();path=self.reviewed_rows([row]);read=self.builder.read
        def changed(target,*args,**kwargs):
            if Path(target)==path:
                path.write_text(dump(dict(records=[])))
            return read(target,*args,**kwargs)
        with patch.object(self.builder,'read',side_effect=changed):
            result=self.run_import([row],require_semantic_review=True)
        self.assertEqual(result['social_exclusion_counts'],{'semantic_review_file_changed_or_hash_mismatch':1})
        self.assertEqual(result['social_new_bodies'],0)

    def test_semantic_gate_preserves_valid_body_co_mentions_and_blocks_rejected_bodies(self):
        self.builder.entity('home','彩虹城','residential','滨江区')
        self.builder.entity('school','杭州市彩虹小学','school','滨江区')
        first=self.row();other=self.row(note_id(4),description=first['record']['desc']+'另一条有待审核内容。')
        self.reviewed_rows([first,other],[dict(id=first['id'],decision='accept',body_sha256=first['body_sha256']),
                                        dict(id=other['id'],decision='review',body_sha256=other['body_sha256'])])
        result=self.run_import([first,other],require_semantic_review=True)
        self.assertEqual((result['social_new_bodies'],result['social_new_co_mentions']),(1,1))
        self.assertEqual(self.db.execute('SELECT post_ids FROM co_mentions').fetchone()[0],dump([first['id']]))
        self.assertEqual(self.db.execute('SELECT count(*) FROM admissions').fetchone()[0],0)

    def test_new_post_district_uses_full_administrative_names_not_brand_or_topics(self):
        for district in ('滨江区','拱墅区','上城区','西湖区','钱塘区'):
            self.assertEqual(new_post_district(district+'看房日记','比较住宅户型和预算。'),district)
            self.assertEqual(new_post_district('看房日记','今天在'+district+'看了一套住宅。'),district)
        self.assertEqual(new_post_district('滨江楼盘看房','滨江集团开发的住宅，杭州买房日记。'),'')
        self.assertEqual(new_post_district('住宅看房','只比较户型。 #滨江区[话题]# #拱墅区买房'),'')
        self.assertEqual(new_post_district('滨江区看房','户型比较。 #拱墅区[话题]#'),'滨江区')

    def test_new_post_district_retains_hints_but_rejects_multiple_regions_or_conflicts(self):
        self.assertEqual(new_post_district('看房','住宅配套。',['滨江']),'滨江区')
        self.assertEqual(new_post_district('看房','住宅配套。',['拱墅区']),'拱墅区')
        self.assertEqual(new_post_district('滨江区看房','住宅配套。',['滨江']),'滨江区')
        self.assertEqual(new_post_district('滨江区看房','住宅配套。',['拱墅']),'')
        self.assertEqual(new_post_district('滨江区看房','住宅配套。',['萧山']),'')
        self.assertEqual(new_post_district('滨江区与拱墅区买房比较','住宅配套。',['滨江']),'')
        self.assertEqual(new_post_district('看房','住宅配套。',['滨江','拱墅']),'')

    def test_new_unlocated_posts_gain_district_but_old_indexes_keep_original_district(self):
        old=self.row(self.old_id)
        first=self.row(note_id(3),description=old['record']['desc']+'本条另行补充新的户型和采光观察。');first['record']['district_hints']=None
        second=self.row(note_id(4),description='拱墅区住宅看房记录，近期比较了一套三房的采光、总价和周边交通，户型是否合适还需要结合家庭通勤预算，价格只作个人购房参考，不是实际成交。')
        second['record']['district_hints']=None
        third=self.row(note_id(5),description='杭州住宅看房记录，滨江集团开发的小区户型和生活配套需要实地确认，购房预算与通勤需求每个人都不同，这里没有说明该项目行政区。 #滨江区[话题]#')
        third['record']['district_hints']=None;third['query']='拱墅区买房'
        result=self.run_import([old,first,second,third])
        self.assertEqual((result['social_new_posts'],result['social_new_bodies']),(3,4))
        actual=dict(self.db.execute('SELECT id,district FROM posts'))
        self.assertEqual(actual[self.old_id],'拱墅区')
        self.assertEqual(actual[first['id']],'滨江区')
        self.assertEqual(actual[second['id']],'拱墅区')
        self.assertEqual(actual[third['id']],'')
        self.assertEqual(self.db.execute('SELECT count(*) FROM entities').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM admissions').fetchone()[0],0)


if __name__ == '__main__':
    unittest.main()
