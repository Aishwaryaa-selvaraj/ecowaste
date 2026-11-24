// Multi-material picker: one weight row per selected material, live per-material values and totals.
// The server recalculates every price and total again when the form is saved.
function initPicker(cats, rows, maxKg) {
  const grid = document.getElementById('matGrid'), body = document.getElementById('matRows'),
        table = document.getElementById('matTable'), empty = document.getElementById('matEmpty'),
        msg = document.getElementById('matMsg');
  const ICON = {recyclable: 'bi-recycle', biodegradable: 'bi-flower1', 'non-recyclable': 'bi-trash3'};
  const byId = new Map(cats.map(c => [String(c.id), c]));
  const chosen = new Map();   // category id -> <tr>
  const round2 = n => Math.round((n + Number.EPSILON) * 100) / 100;

  cats.forEach(c => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'mat-card'; b.dataset.id = c.id; b.setAttribute('aria-pressed', 'false');
    b.innerHTML = '<i class="bi ' + (ICON[c.type] || 'bi-box') + '"></i><span class="n"></span><small></small>';
    b.querySelector('.n').textContent = c.name;
    b.querySelector('small').textContent = c.cur ? inr(c.cur) + '/kg' : 'No payout';
    b.onclick = () => chosen.has(String(c.id)) ? remove(String(c.id)) : add(String(c.id), '');
    grid.appendChild(b);
  });

  function add(id, weight) {
    const c = byId.get(String(id));
    if (!c || chosen.has(id)) return;                       // duplicates are impossible in the UI
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><b class="nm"></b><input type="hidden" name="category_id"></td>' +
      '<td><div class="input-group"><input class="form-control" type="number" name="weight" min="0.01" max="' + maxKg +
      '" step="0.01" inputmode="decimal" required><span class="input-group-text">kg</span></div></td>' +
      '<td class="text-end pr"></td><td class="text-end fw-semibold val">₹0.00</td>' +
      '<td><button type="button" class="btn btn-sm btn-outline-danger px-2" title="Remove"><i class="bi bi-x-lg"></i></button></td>';
    tr.querySelector('.nm').textContent = c.name;
    tr.querySelector('[name=category_id]').value = c.id;
    tr.querySelector('.pr').textContent = inr(c.cur);
    const w = tr.querySelector('[name=weight]'); w.value = weight || '';
    w.setAttribute('aria-label', 'Weight of ' + c.name + ' in kg');
    w.oninput = recalc;
    tr.querySelector('button').onclick = () => remove(id);
    body.appendChild(tr); chosen.set(id, tr);
    grid.querySelector('[data-id="' + id + '"]').classList.add('on');
    grid.querySelector('[data-id="' + id + '"]').setAttribute('aria-pressed', 'true');
    recalc(); if (!weight) w.focus();
  }
  function remove(id) {
    chosen.get(id).remove(); chosen.delete(id);
    const b = grid.querySelector('[data-id="' + id + '"]'); b.classList.remove('on'); b.setAttribute('aria-pressed', 'false');
    recalc();
  }
  function recalc() {
    let kg = 0, total = 0;
    chosen.forEach((tr, id) => {
      const w = Math.max(parseFloat(tr.querySelector('[name=weight]').value) || 0, 0), v = round2(w * (byId.get(id).cur || 0));
      tr.querySelector('.val').textContent = inr(v); kg += w; total += v;
    });
    document.getElementById('matKg').textContent = (Math.round(kg * 100) / 100) + ' kg';
    document.getElementById('matTotal').textContent = inr(round2(total));
    table.classList.toggle('d-none', !chosen.size); empty.classList.toggle('d-none', chosen.size > 0);
    msg.classList.add('d-none');
  }
  // Returns true when every selected material has a valid weight; otherwise shows what is wrong.
  function validate() {
    let err = '';
    if (!chosen.size) err = 'Select at least one waste material.';
    else chosen.forEach((tr, id) => {
      const w = parseFloat(tr.querySelector('[name=weight]').value);
      if (!err && !(w > 0 && w <= maxKg)) err = 'Enter a valid weight for ' + byId.get(id).name + ' (more than 0 and up to ' + maxKg + ' kg).';
    });
    msg.textContent = err; msg.classList.toggle('d-none', !err);
    return !err;
  }
  const selection = () => [...chosen].map(([id, tr]) => id + ':' + (parseFloat(tr.querySelector('[name=weight]').value) || '')).join(',');
  (rows || []).forEach(r => add(String(r.id), r.weight));
  recalc();
  return {validate, selection};
}
