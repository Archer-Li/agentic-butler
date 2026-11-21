'use strict';

const deck = document.querySelector('#deck');
const tabs = [...deck.querySelectorAll('[data-slide]')];
const panels = tabs.map(tab => document.querySelector('#slide-' + tab.dataset.slide));
const previous = deck.querySelector('#previous-slide');
const next = deck.querySelector('#next-slide');
let current = 0;

function showSlide(index, focusTab = false) {
  current = (index + tabs.length) % tabs.length;
  tabs.forEach((tab, position) => {
    const active = position === current;
    tab.setAttribute('aria-selected', String(active));
    tab.tabIndex = active ? 0 : -1;
    panels[position].hidden = !active;
  });
  previous.disabled = current === 0;
  next.disabled = current === tabs.length - 1;
  deck.querySelector('#slide-counter').textContent =
    (current + 1) + ' / ' + tabs.length + ' · ' + tabs[current].textContent;
  if (focusTab) tabs[current].focus();
}

const tabList = deck.querySelector('.tabs');
tabList.setAttribute('role', 'tablist');
tabs.forEach((tab, index) => {
  tab.setAttribute('role', 'tab');
  tab.setAttribute('aria-controls', panels[index].id);
  panels[index].setAttribute('role', 'tabpanel');
  panels[index].setAttribute('aria-labelledby', tab.id);
  panels[index].tabIndex = 0;
  tab.addEventListener('click', () => showSlide(index));
});
tabList.addEventListener('keydown', event => {
  const destinations = {ArrowLeft: current - 1, ArrowRight: current + 1, Home: 0, End: tabs.length - 1};
  if (!Object.hasOwn(destinations, event.key)) return;
  event.preventDefault();
  showSlide(destinations[event.key], true);
});
function step(offset, button) {
  showSlide(current + offset);
  if (document.activeElement === button && button.disabled) tabs[current].focus();
}
previous.addEventListener('click', () => step(-1, previous));
next.addEventListener('click', () => step(1, next));
showSlide(0);
tabList.hidden = false;
deck.querySelector('.controls').hidden = false;
