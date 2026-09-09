import {test} from 'node:test';
import assert from 'node:assert/strict';
import {icon, createIcon, enhanceIcons, ICON_NAMES, ICON_SOURCES, ICON_PACKAGE} from '../web/icons.js';

test('all application aliases are backed by the pinned free Hugeicons package', () => {
  const expected = ['search','chevron-down','chevron-up','chevron-right','arrow-left','arrow-right','external','close','plus','minus','school','home','layers','filter','expand','locate','star','check','help','info','compare','folder','document','trash','link','calendar','reset'];
  assert.deepEqual([...ICON_NAMES].sort(), expected.sort());
  assert.deepEqual(ICON_PACKAGE, {name:'@hugeicons/core-free-icons',version:'4.3.2',license:'MIT'});
  for (const name of ICON_NAMES) {
    const svg = icon(name);
    assert.ok(svg.includes(`data-hugeicon="${ICON_SOURCES[name]}"`));
    assert.match(svg, /viewBox="0 0 24 24"/);
    assert.match(svg, /aria-hidden="true"/);
    assert.match(svg, /focusable="false"/);
    assert.match(svg, /stroke="currentColor"/);
    assert.doesNotMatch(svg, /<script|<image|\shref=|url\(/);
  }
});

test('icon options preserve requested size and escape arbitrary class attributes', () => {
  const svg = icon('search', {size:20,className:'extra" onclick="bad()'});
  assert.match(svg, /width="20" height="20"/);
  assert.match(svg, /--icon-size:20px/);
  assert.match(svg, /extra&quot; onclick=&quot;bad\(\)/);
  assert.doesNotMatch(svg, /" onclick="/);
  assert.match(icon('search',{size:NaN}), /width="16"/);
  assert.throws(() => icon('invented-icon'), RangeError);
});

test('control arrows and checkmarks preserve the selected official icon paths', () => {
  assert.equal(ICON_SOURCES['chevron-down'], 'ArrowDown01Icon');
  assert.equal(ICON_SOURCES.check, 'Tick02Icon');
  assert.equal(ICON_SOURCES.expand, 'FullScreenIcon');
  assert.equal(ICON_SOURCES.locate, 'Gps01Icon');
  assert.match(icon('chevron-down'), /M18 9\.00005C18 9\.00005/);
  assert.match(icon('check'), /M5 14L8\.5 17\.5L19 6\.5/);
});

class Element {
  constructor(name,namespace,doc) { this.name=name; this.namespaceURI=namespace; this.ownerDocument=doc; this.attributes=new Map(); this.children=[]; }
  setAttribute(name,value) { this.attributes.set(name,String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  append(node) { this.children.push(node); }
  replaceChildren(...nodes) { this.children=nodes; }
  get firstElementChild() { return this.children[0] ?? null; }
  matches(selector) { return selector==='[data-icon]' && this.attributes.has('data-icon'); }
  querySelectorAll(selector) { return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]); }
}
const doc = {createElementNS(namespace,name) { return new Element(name,namespace,this); }};

test('DOM icons use SVG namespace and enhancement retains host classes and is idempotent', () => {
  const svg = createIcon('school',doc);
  assert.equal(svg.namespaceURI,'http://www.w3.org/2000/svg');
  assert.ok(svg.children.every(node=>node.namespaceURI===svg.namespaceURI));
  assert.equal(svg.getAttribute('aria-hidden'),'true');
  const host=new Element('span','html',doc); host.setAttribute('class','entity-icon home');host.setAttribute('data-icon','home');
  assert.equal(enhanceIcons(host),1);
  assert.equal(host.getAttribute('class'),'entity-icon home');
  assert.equal(host.firstElementChild.getAttribute('data-hugeicon'),'Home01Icon');
  assert.equal(enhanceIcons(host),0);
});
