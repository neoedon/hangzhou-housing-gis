import {createIcon} from './icons.js';

const enhanced = new Set();
const bySelect = new WeakMap();
const observedDocuments = new WeakSet();
let current = null;
let nextId = 0;

const normalize = value => String(value ?? '').normalize('NFKC').replace(/\s+/g, '').toLowerCase();

export function filterSelectOptions(options, query = '') {
  const q = normalize(query);
  return options.filter(option => !q || normalize([option.label, option.value, option.group].join(' ')).includes(q));
}

export function selectMenuPosition(anchor, viewport, desiredHeight = 320) {
  const margin = 8, gap = 6;
  const viewportWidth = Math.max(0, Number(viewport.width) || 0);
  const viewportHeight = Math.max(0, Number(viewport.height) || 0);
  const width = Math.min(Math.max(Number(anchor.width) || 0, 200), Math.max(0, viewportWidth - margin * 2));
  const wanted = Math.min(320, Math.max(0, Number(desiredHeight) || 0));
  const below = Math.max(0, viewportHeight - margin - anchor.bottom - gap);
  const above = Math.max(0, anchor.top - margin - gap);
  const placement = below < wanted && above > below ? 'top' : 'bottom';
  const maxHeight = Math.min(wanted, placement === 'top' ? above : below);
  const left = Math.max(margin, Math.min(anchor.left, viewportWidth - width - margin));
  const top = Math.max(margin, Math.min(placement === 'top' ? anchor.top - gap - maxHeight : anchor.bottom + gap,
    viewportHeight - maxHeight - margin));
  return {left, top, width, maxHeight, placement};
}

function fieldLabel(select) {
  const explicit = select.getAttribute('aria-label');
  if (explicit) return explicit;
  const labelledBy = select.getAttribute('aria-labelledby');
  if (labelledBy) {
    const text = labelledBy.split(/\s+/).map(id => select.ownerDocument.getElementById(id)?.textContent || '').join(' ').trim();
    if (text) return text;
  }
  const labels = [...(select.labels || [])].map(label => {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('select,button,input,.enhanced-select').forEach(node => node.remove());
    return copy.textContent.trim();
  }).filter(Boolean);
  return labels.join(' / ') || select.name || '选择选项';
}

function readOptions(select) {
  return [...select.options].map((option, index) => ({
    index, value:option.value, label:option.label || option.textContent || option.value,
    disabled:option.disabled || (option.parentElement?.tagName === 'OPTGROUP' && option.parentElement.disabled),
    group:option.parentElement?.tagName === 'OPTGROUP' ? option.parentElement.label : ''
  }));
}

function syncEntry(entry) {
  const {select, button, value} = entry;
  const option = select.options[select.selectedIndex];
  entry.label = fieldLabel(select);
  value.textContent = option?.label || option?.textContent || '请选择';
  button.setAttribute('aria-label', `${entry.label}：${value.textContent}`);
  button.disabled = select.disabled;
  button.setAttribute('aria-required', String(select.required));
  button.classList.toggle('has-value', !!select.value);
  if (select.hasAttribute('aria-invalid')) button.setAttribute('aria-invalid', select.getAttribute('aria-invalid'));
  else button.removeAttribute('aria-invalid');
  if (select.hasAttribute('aria-describedby')) button.setAttribute('aria-describedby', select.getAttribute('aria-describedby'));
  else button.removeAttribute('aria-describedby');
}

export function isSelectMenuOpen() { return !!current; }

export function closeSelectMenu(restoreFocus = false) {
  if (!current) return;
  const menu = current;
  current = null;
  menu.popover.remove();
  menu.entry.button.setAttribute('aria-expanded', 'false');
  menu.entry.button.removeAttribute('aria-activedescendant');
  menu.entry.button.classList.remove('is-open');
  if (restoreFocus && menu.entry.button.isConnected && !menu.entry.button.disabled) menu.entry.button.focus({preventScroll:true});
}

function setActive(index, scroll = false) {
  if (!current) return;
  const menu = current;
  menu.active = index;
  for (const option of menu.list.children) option.classList.toggle('is-active', Number(option.dataset.optionIndex) === index);
  const node = [...menu.list.children].find(option => Number(option.dataset.optionIndex) === index);
  const focusOwner = menu.search || menu.entry.button;
  if (node) focusOwner.setAttribute('aria-activedescendant', node.id);
  else focusOwner.removeAttribute('aria-activedescendant');
  if (scroll && node) node.scrollIntoView({block:'nearest'});
}

function positionMenu() {
  if (!current) return;
  const {entry, popover, list, search, options} = current;
  const view = entry.select.ownerDocument.defaultView;
  const visible = filterSelectOptions(options, search?.value || '');
  const height = Math.min(320, Math.max(58, visible.length * 40 + (search ? 58 : 12)));
  const position = selectMenuPosition(entry.button.getBoundingClientRect(), {width:view.innerWidth, height:view.innerHeight}, height);
  Object.assign(popover.style, {position:'fixed', left:`${position.left}px`, top:`${position.top}px`, width:`${position.width}px`,
    maxHeight:`${position.maxHeight}px`, boxSizing:'border-box', zIndex:'10000', overflow:'hidden'});
  popover.dataset.placement = position.placement;
  list.style.maxHeight = `${Math.max(0, position.maxHeight - (search ? 58 : 12))}px`;
  list.style.overflowY = 'auto';
  list.style.overscrollBehavior = 'contain';
}

function renderOptions(preferSelected = false) {
  if (!current) return;
  const menu = current, doc = menu.entry.select.ownerDocument;
  menu.visible = filterSelectOptions(menu.options, menu.search?.value || '');
  const nodes = menu.visible.map(option => {
    const node = doc.createElement('div');
    node.className = 'select-option';
    node.id = `${menu.entry.id}-option-${option.index}`;
    node.setAttribute('role', 'option');
    node.setAttribute('aria-selected', String(option.index === menu.entry.select.selectedIndex));
    node.setAttribute('aria-disabled', String(!!option.disabled));
    node.dataset.optionIndex = String(option.index);
    node.textContent = option.label;
    const check = doc.createElement('span');
    check.className = 'select-check';
    check.setAttribute('aria-hidden', 'true');
    check.append(createIcon('check', doc));
    node.append(check);
    if (option.group) node.title = option.group;
    return node;
  });
  menu.list.replaceChildren(...nodes);
  menu.status.textContent = menu.visible.length ? '' : '没有匹配选项';
  menu.status.hidden = menu.visible.length > 0;
  const enabled = menu.visible.filter(option => !option.disabled);
  const preferred = preferSelected ? menu.entry.select.selectedIndex : menu.active;
  setActive(enabled.some(option => option.index === preferred) ? preferred : enabled[0]?.index ?? -1);
  positionMenu();
}

function choose(index) {
  if (!current) return;
  const {entry, options} = current;
  const option = options.find(item => item.index === index);
  if (!option || option.disabled || entry.select.disabled) return;
  const previous = entry.select.selectedIndex;
  entry.select.selectedIndex = option.index;
  syncEntry(entry);
  closeSelectMenu(true);
  if (previous !== option.index) entry.select.dispatchEvent(new entry.select.ownerDocument.defaultView.Event('change', {bubbles:true}));
}

function openMenu(entry, initial = '') {
  if (entry.select.disabled) return;
  closeSelectMenu();
  const doc = entry.select.ownerDocument, popover = doc.createElement('div'), list = doc.createElement('div');
  const options = readOptions(entry.select), status = doc.createElement('div');
  popover.className = 'select-popover';
  list.className = 'select-options';
  list.id = `${entry.id}-listbox`;
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', entry.label);
  status.className = 'select-menu-status';
  status.setAttribute('role', 'status');
  let search = null;
  if (options.length > 7) {
    search = doc.createElement('input');
    search.className = 'select-menu-search';
    search.type = 'search';
    search.placeholder = `搜索${entry.label}…`;
    search.autocomplete = 'off';
    search.spellcheck = false;
    search.setAttribute('role', 'combobox');
    search.setAttribute('aria-label', `搜索${entry.label}`);
    search.setAttribute('aria-autocomplete', 'list');
    search.setAttribute('aria-expanded', 'true');
    search.setAttribute('aria-controls', list.id);
    search.addEventListener('input', () => renderOptions());
    popover.append(search);
  }
  popover.append(list, status);
  (entry.button.closest('dialog[open]') || doc.body).append(popover);
  current = {entry, popover, list, status, search, options, visible:[], active:-1, typeahead:'', typedAt:0};
  entry.button.setAttribute('aria-expanded', 'true');
  entry.button.classList.add('is-open');
  entry.button.setAttribute('aria-controls', list.id);
  popover.addEventListener('click', event => {
    event.stopPropagation();
    const option = event.target.closest('[data-option-index]');
    if (option && popover.contains(option)) choose(Number(option.dataset.optionIndex));
  });
  popover.addEventListener('pointermove', event => {
    const option = event.target.closest('[data-option-index]');
    if (option && option.getAttribute('aria-disabled') !== 'true') setActive(Number(option.dataset.optionIndex));
  });
  if (search) search.value = initial;
  renderOptions(!initial);
  if (search) search.focus({preventScroll:true});
  else entry.button.focus({preventScroll:true});
  if (initial && !search) typeAhead(initial);
  setActive(current.active, true);
}

function moveActive(key) {
  const enabled = current.visible.filter(option => !option.disabled);
  if (!enabled.length) return;
  const index = enabled.findIndex(option => option.index === current.active);
  const next = key === 'Home' ? 0 : key === 'End' ? enabled.length - 1 :
    key === 'ArrowDown' ? Math.min(enabled.length - 1, index + 1) : Math.max(0, index < 0 ? enabled.length - 1 : index - 1);
  setActive(enabled[next].index, true);
}

function typeAhead(key) {
  if (!current) return;
  const now = Date.now();
  current.typeahead = now - current.typedAt > 750 ? key : current.typeahead + key;
  current.typedAt = now;
  const query = normalize(current.typeahead);
  const option = current.visible.find(item => !item.disabled && normalize(item.label).startsWith(query));
  if (option) setActive(option.index, true);
}

function menuKeydown(event) {
  if (!current || event.isComposing || event.keyCode === 229) return;
  const menu = current;
  if (event.target !== menu.entry.button && !menu.popover.contains(event.target)) return;
  if (event.key === 'Escape') {
    event.preventDefault(); event.stopPropagation(); closeSelectMenu(true); return;
  }
  if (event.key === 'Tab') {
    // Resume natural tab order from the trigger rather than the end-of-body portal.
    closeSelectMenu(true); return;
  }
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault(); event.stopPropagation(); moveActive(event.key); return;
  }
  if (event.key === 'Enter' || event.key === ' ' && event.target !== menu.search) {
    event.preventDefault(); event.stopPropagation(); choose(menu.active); return;
  }
  if (event.target !== menu.search && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault(); event.stopPropagation(); typeAhead(event.key);
  }
}

function observe(doc) {
  if (observedDocuments.has(doc)) return;
  observedDocuments.add(doc);
  const outside = event => {
    if (current && event.target !== current.entry.button && !current.entry.button.contains(event.target) && !current.popover.contains(event.target)) closeSelectMenu();
  };
  doc.addEventListener('pointerdown', outside, true);
  doc.addEventListener('click', outside, true);
  doc.addEventListener('focusin', outside, true);
  doc.addEventListener('keydown', menuKeydown, true);
  doc.addEventListener('scroll', event => {
    if (current && !current.popover.contains(event.target)) closeSelectMenu();
  }, {capture:true, passive:true});
  doc.addEventListener('reset', () => setTimeout(syncSelects, 0), true);
  doc.defaultView.addEventListener('resize', () => closeSelectMenu(), {passive:true});
  doc.defaultView.visualViewport?.addEventListener('resize', () => closeSelectMenu(), {passive:true});
}

export function enhanceSelects(root = document) {
  const selects = [...(root.matches?.('select') ? [root] : []), ...root.querySelectorAll('select')];
  for (const select of selects) {
    if (bySelect.has(select) || select.multiple || select.size > 1 || select.hidden) continue;
    const doc = select.ownerDocument, wrapper = doc.createElement('span'), button = doc.createElement('button');
    const value = doc.createElement('span'), chevron = doc.createElement('span');
    const entry = {select, wrapper, button, value, label:fieldLabel(select), id:`enhanced-select-${++nextId}`};
    wrapper.className = 'enhanced-select';
    button.className = 'select-trigger';
    button.type = 'button';
    button.id = `${select.id || entry.id}-trigger`;
    button.setAttribute('role', 'combobox');
    button.setAttribute('aria-haspopup', 'listbox');
    button.setAttribute('aria-expanded', 'false');
    value.className = 'select-trigger-value';
    chevron.className = 'select-chevron';
    chevron.append(createIcon('chevron-down', doc));
    chevron.setAttribute('aria-hidden', 'true');
    button.append(value, chevron);
    select.before(wrapper);
    wrapper.append(select, button);
    syncEntry(entry);
    button.addEventListener('click', event => {
      event.stopPropagation();
      if (current?.entry === entry) closeSelectMenu(); else openMenu(entry);
    });
    button.addEventListener('keydown', event => {
      if (current || event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
      if (['ArrowDown', 'ArrowUp', 'Enter', ' ', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); event.stopPropagation(); openMenu(entry);
        if (['Home', 'End'].includes(event.key)) moveActive(event.key);
      } else if (event.key.length === 1) {
        event.preventDefault(); event.stopPropagation(); openMenu(entry, event.key);
      }
    });
    select.addEventListener('change', () => syncEntry(entry));
    // Hide the native control only after its replacement and event handling exist.
    select.hidden = true;
    select.style.display = 'none';
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    enhanced.add(entry); bySelect.set(select, entry); observe(doc);
  }
  syncSelects();
}

export function syncSelects() {
  for (const entry of enhanced) {
    if (!entry.button.isConnected || !entry.select.isConnected) {
      if (current?.entry === entry) closeSelectMenu();
      enhanced.delete(entry); continue;
    }
    syncEntry(entry);
    if (current?.entry === entry) {
      if (entry.select.disabled) closeSelectMenu();
      else { current.options = readOptions(entry.select); renderOptions(true); }
    }
  }
}
