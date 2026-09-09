import {test} from 'node:test';
import assert from 'node:assert/strict';
import {filterSelectOptions, selectMenuPosition} from '../web/controls.js';

test('select search handles Chinese spacing, values and grouped labels without mutation', () => {
  const options = [{label:'滨江区', value:'binjiang'}, {label:'拱墅区', value:'gongshu', group:'杭州主城'}, {label:'其他', value:'other', disabled:true}];
  assert.deepEqual(filterSelectOptions(options, ' 拱 墅 '), [options[1]]);
  assert.deepEqual(filterSelectOptions(options, 'ＧＯＮＧＳＨＵ'), [options[1]]);
  assert.deepEqual(filterSelectOptions(options, '主城'), [options[1]]);
  assert.deepEqual(filterSelectOptions(options, ''), options);
  assert.deepEqual(filterSelectOptions(options, '不存在'), []);
  assert.equal(options.length, 3);
});

test('select menu opens below with a trigger-width minimum and 320px height cap', () => {
  const p = selectMenuPosition({left:30, top:40, bottom:80, width:250}, {width:900, height:800}, 600);
  assert.deepEqual(p, {left:30, top:86, width:250, maxHeight:320, placement:'bottom'});
});

test('select menu opens above near the bottom and clamps to the right viewport edge', () => {
  const p = selectMenuPosition({left:810, top:710, bottom:750, width:150}, {width:900, height:800}, 280);
  assert.deepEqual(p, {left:692, top:424, width:200, maxHeight:280, placement:'top'});
});

test('small viewport constrains both menu width and available scrolling height', () => {
  const p = selectMenuPosition({left:12, top:65, bottom:105, width:300}, {width:180, height:170});
  assert.equal(p.width, 164); assert.equal(p.left, 8); assert.equal(p.placement, 'bottom');
  assert.equal(p.maxHeight, 51); assert.equal(p.top, 111);
  assert.ok(p.left + p.width <= 172); assert.ok(p.top + p.maxHeight <= 162);
});

test('empty or short select menus use their content height rather than reserving 320px', () => {
  const p = selectMenuPosition({left:20, top:50, bottom:90, width:180}, {width:500, height:600}, 92);
  assert.equal(p.maxHeight, 92); assert.equal(p.placement, 'bottom'); assert.equal(p.width, 200);
});
