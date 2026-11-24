# ECOWASTE - Smart Household Waste Collection, Recycling & Value Estimation

Flask + MySQL web app with exactly **three roles**: Household, Waste Collector, Recycler. There is no admin role, table or page.

## Quick start
```bash
pip install -r requirements.txt
mysql -u root -p < database.sql          # NEW install: creates DB `ecowaste` (re-running resets all data)
# Already have data from the older single-material version? Back it up, then run this INSTEAD of database.sql:
#   mysqldump -u root -p ecowaste > backup.sql && mysql -u root -p ecowaste < migrate.sql
export DB_PASSWORD=your_mysql_password   # Windows: set DB_PASSWORD=...  (also DB_USER / DB_HOST / DB_NAME)
flask --app app seed-demo                # optional demo accounts
python app.py                            # http://127.0.0.1:5000
```
Demo logins (password `Demo@1234`): `household@demo.com`, `collector@demo.com`, `recycler@demo.com`.
Set `SECRET_KEY` to a random value for anything beyond a demo.

## Demo script (3 browser windows / profiles)
1. **Household**: Estimator (add several materials) → Book Pickup: choose materials (e.g. Paper 5 kg, Plastic 3 kg, Glass 2 kg), enter address + PIN, submit → Request Status.
2. **Collector**: Pickup Requests → Accept → My Pickups: *Start collection* → enter the actual weight of each material → *Mark waste collected* (value and payment are recorded) → choose the recycler → *Mark delivered to recycler*.
3. **Recycler**: Deliveries → *Confirm waste received* → *Start recycling* → *Mark process completed*.
4. **Household**: Request Status / Collection History → itemised breakdown, Receipt (print/download), rate the collector.

## How things work
- **Status flow** (one status per request, shown to all roles): Request Submitted → Request Accepted → Collection in Progress → Waste Collected → Waste Delivered to Recycler → Process Completed (plus Cancelled by the household while still pending). While a request is *Delivered* the recycler's step (received / recycling in progress) is shown too.
- **Several materials per request**: `request_items` holds each requested material (weight, price/kg, value); `waste_records` holds what the collector actually weighed per material. `collection_requests` keeps the totals (requested and final). A material can appear only once per request.
- **Value**: per material `weight × price per kg`; total = sum of the materials. Prices come from the latest row in `waste_prices`. The browser shows live figures, but the server recalculates every price and total itself before saving. The estimate uses the price at booking; the final value uses the price at weighing.
- **Pickup address**: address, PIN code and optional landmark are validated and saved on the request as one complete address, shown to the collector before and after accepting (with an *Open in maps* link). A map pin is optional and never required to book.
- **Payments are simulated** (sandbox): when the collector marks the waste collected, matching debit & credit rows are written in `transactions`. No real money moves.
- **Collector earnings** = marketplace sales received − payouts made to households.
- **Stock & marketplace**: weighed waste becomes stock per material and can be listed for recyclers; once a request is delivered to a recycler its stock is cleared.
- **Waste scanner**: runs MobileNet (TensorFlow.js) *in the browser* and maps ImageNet labels to ECOWASTE categories. It is approximate, the photo is never uploaded, and it needs internet the first time to download the model. Manual classification is always available.
- **Languages**: English/Tamil switcher uses a built-in dictionary in `static/js/app.js` (works offline, no API key). Add a language by adding a key to `I18N`.
- **Maps**: Leaflet + OpenStreetMap tiles (free) are used only for the optional pin on the booking page.
- **Forgot password**: generates a 30-minute single-use link. With no email server, demo mode (`DEMO_SHOW_RESET_LINK=1`, default) shows the link on screen. Set it to `0` and add SMTP sending before real use.

## Security
Password hashing (Werkzeug), parameterised SQL everywhere, CSRF tokens on every POST, role checks on every blueprint, session cookies HttpOnly + SameSite, input validation server-side, no file uploads reach the server.

## Placeholders to replace
- `waste_prices` history is sample data - insert new rows (new `effective_date`) to change live prices.
- Contact email on the home page.

## Structure
```
app.py  config.py  db.py  utils.py  database.sql  migrate.sql  requirements.txt
routes/  auth.py household.py collector.py recycler.py
templates/  base.html dash.html _macros.html index/login/register/... household/ collector/ recycler/
static/  css/style.css  js/app.js  js/materials.js  images/logo.svg
```
