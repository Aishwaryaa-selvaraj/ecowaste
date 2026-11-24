-- ECOWASTE upgrade script: old single-material schema -> multi-material schema.
-- Run ONCE on an existing database (back it up first:  mysqldump ecowaste > backup.sql):
--     mysql -u root -p ecowaste < migrate.sql
-- It keeps all existing users, requests, weighings, payments and ratings.
-- Fresh installs should use database.sql instead.

-- 1. Materials of a request now live in their own table.
CREATE TABLE IF NOT EXISTS request_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  category_id INT NOT NULL,
  est_weight DECIMAL(8,2) NOT NULL,
  price_per_kg DECIMAL(10,2) NOT NULL,
  est_value DECIMAL(10,2) NOT NULL,
  UNIQUE KEY uq_item (request_id, category_id),
  FOREIGN KEY (request_id) REFERENCES collection_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (category_id) REFERENCES waste_categories(id)
) ENGINE=InnoDB;

-- 2. Carry every existing single-material request over as one item.
INSERT IGNORE INTO request_items (request_id, category_id, est_weight, price_per_kg, est_value)
SELECT id, category_id, est_weight, IF(est_weight > 0, ROUND(est_value / est_weight, 2), 0), est_value
FROM collection_requests WHERE category_id IS NOT NULL;

-- 3. New request columns and the new status list (on_the_way is kept until the data is converted).
ALTER TABLE collection_requests
  MODIFY category_id INT NULL,          -- legacy column, no longer written; kept so old rows stay intact
  ADD COLUMN recycler_id INT NULL AFTER collector_id,
  ADD COLUMN final_weight DECIMAL(8,2) NULL AFTER est_value,
  ADD COLUMN final_value DECIMAL(10,2) NULL AFTER final_weight,
  ADD COLUMN recycling_stage ENUM('received','processing') NULL,
  ADD COLUMN delivered_at DATETIME NULL,
  ADD INDEX idx_req_recycler (recycler_id),
  ADD CONSTRAINT fk_req_recycler FOREIGN KEY (recycler_id) REFERENCES recyclers(user_id) ON DELETE SET NULL,
  MODIFY status ENUM('pending','assigned','on_the_way','in_progress','collected','delivered','completed','cancelled')
    NOT NULL DEFAULT 'pending';
UPDATE collection_requests SET status = 'in_progress' WHERE status = 'on_the_way';
ALTER TABLE collection_requests
  MODIFY status ENUM('pending','assigned','in_progress','collected','delivered','completed','cancelled')
    NOT NULL DEFAULT 'pending';

-- 4. Totals for requests that were already weighed.
UPDATE collection_requests r
JOIN (SELECT request_id, SUM(actual_weight) w, SUM(final_value) v FROM waste_records GROUP BY request_id) x ON x.request_id = r.id
SET r.final_weight = x.w, r.final_value = x.v;

-- 5. waste_records: one row per request AND material (was one per request).
ALTER TABLE waste_records ADD UNIQUE KEY uq_rec_item (request_id, category_id);
ALTER TABLE waste_records DROP INDEX request_id;

-- 6. Remove live tracking data and the Recycling Centres feature.
ALTER TABLE collectors DROP COLUMN lat, DROP COLUMN lng, DROP COLUMN loc_updated;
DROP TABLE IF EXISTS recycling_centres;
