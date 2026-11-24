// Shared helpers: CSRF-aware POST, English/Tamil switcher (add more languages by adding a key to I18N).
const csrf = () => document.querySelector('meta[name=csrf]').content;
const postJSON = (url, data) => fetch(url, {
  method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf()}, body: JSON.stringify(data)
}).then(r => r.json());
const inr = n => '₹' + Number(n || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});

const I18N = {
  ta: {
    nav_home: 'முகப்பு', nav_how: 'செயல்படும் விதம்', nav_features: 'அம்சங்கள்', nav_contact: 'தொடர்பு',
    login: 'உள்நுழை', register: 'பதிவு செய்', logout: 'வெளியேறு', profile: 'சுயவிவரம்', notifications: 'அறிவிப்புகள்',
    hero_title: 'குப்பையை மதிப்பாக மாற்றுங்கள்',
    hero_sub: 'வீடுகள், குப்பை சேகரிப்பாளர்கள் மற்றும் மறுசுழற்சியாளர்களை இணைக்கும் ஸ்மார்ட் தளம்.',
    how_title: 'செயல்படும் விதம்', features_title: 'எங்கள் அம்சங்கள்', aware_title: 'சுற்றுச்சூழல் விழிப்புணர்வு',
    stats_title: 'மறுசுழற்சி புள்ளிவிவரங்கள்', contact_title: 'தொடர்புக்கு', get_started: 'தொடங்குங்கள்',
    dashboard: 'டாஷ்போர்டு', scanner: 'கழிவு ஸ்கேனர்', estimator: 'மதிப்பீட்டுக் கருவி', prices: 'விலை போக்குகள்',
    book: 'பிக்கப் பதிவு', status: 'கோரிக்கை நிலை', history: 'சேகரிப்பு வரலாறு', analytics: 'மாதாந்திர பகுப்பாய்வு',
    requests: 'பிக்கப் கோரிக்கைகள்', pickups: 'என் பிக்கப்கள்', inventory: 'என் இருப்பு',
    orders: 'ஆர்டர்கள்', earnings: 'வருமானம்', market: 'சந்தை', deliveries: 'விநியோகங்கள்'
  }
};
function applyLang() {
  const l = localStorage.getItem('lang') || 'en';
  document.documentElement.lang = l;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    if (!el.dataset.en) el.dataset.en = el.textContent;
    el.textContent = (I18N[l] && I18N[l][el.dataset.i18n]) || el.dataset.en;
  });
  document.querySelectorAll('.lang-btn').forEach(b => b.classList.toggle('on', b.dataset.lang === l));
}
function setLang(l) { try { localStorage.setItem('lang', l); } catch (e) {} applyLang(); }
document.addEventListener('DOMContentLoaded', applyLang);
