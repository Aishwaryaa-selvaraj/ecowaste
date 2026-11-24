-- ECOWASTE database schema (MySQL 8 / MariaDB 10.4+)
-- WARNING: re-running this file drops and recreates all tables.
-- Three roles only: household, collector, recycler. There is NO admin table or role.
-- (Upgrading an existing database? Do NOT run this file - run migrate.sql instead, it keeps your data.)

CREATE DATABASE IF NOT EXISTS ecowaste CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ecowaste;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS feedback, notifications, transactions, marketplace_orders, waste_records,
  pickup_assignments, request_items, collection_requests, waste_prices, waste_categories, recycling_centres,
  recyclers, collectors, households, users;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(150) NOT NULL UNIQUE,
  phone VARCHAR(15) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('household','collector','recycler') NOT NULL,
  reset_token VARCHAR(64) NULL,
  reset_expires DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE households (
  user_id INT PRIMARY KEY,
  address VARCHAR(255) NOT NULL,
  lat DECIMAL(9,6) NULL,
  lng DECIMAL(9,6) NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE collectors (
  user_id INT PRIMARY KEY,
  vehicle VARCHAR(60) NOT NULL,
  area VARCHAR(100) NOT NULL,
  available TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE recyclers (
  user_id INT PRIMARY KEY,
  company VARCHAR(120) NOT NULL,
  address VARCHAR(255) NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE waste_categories (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(60) NOT NULL UNIQUE,
  type ENUM('biodegradable','recyclable','non-recyclable') NOT NULL,
  description VARCHAR(255) NULL,
  default_price DECIMAL(10,2) NOT NULL DEFAULT 0   -- reference price used only to seed price history
) ENGINE=InnoDB;

CREATE TABLE waste_prices (
  id INT AUTO_INCREMENT PRIMARY KEY,
  category_id INT NOT NULL,
  price_per_kg DECIMAL(10,2) NOT NULL,
  effective_date DATE NOT NULL,
  INDEX (category_id, effective_date),
  FOREIGN KEY (category_id) REFERENCES waste_categories(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- One row per household request. A request holds several materials (see request_items).
CREATE TABLE collection_requests (
  id INT AUTO_INCREMENT PRIMARY KEY,
  household_id INT NOT NULL,
  collector_id INT NULL,
  recycler_id INT NULL,                       -- recycler the collector delivered to
  est_weight DECIMAL(8,2) NOT NULL,           -- total requested weight  = SUM(request_items.est_weight)
  est_value DECIMAL(10,2) NOT NULL,           -- total estimated value   = SUM(request_items.est_value)
  final_weight DECIMAL(8,2) NULL,             -- total actual weight     = SUM(waste_records.actual_weight)
  final_value DECIMAL(10,2) NULL,             -- total final value       = SUM(waste_records.final_value)
  pickup_date DATE NOT NULL,
  pickup_time TIME NOT NULL,
  address VARCHAR(255) NOT NULL,              -- complete pickup address (what the collector sees)
  lat DECIMAL(9,6) NULL,                      -- optional map pin
  lng DECIMAL(9,6) NULL,
  status ENUM('pending','assigned','in_progress','collected','delivered','completed','cancelled') NOT NULL DEFAULT 'pending',
  recycling_stage ENUM('received','processing') NULL,   -- set by the recycler while status = 'delivered'
  delivered_at DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (household_id), INDEX (collector_id), INDEX (recycler_id), INDEX (status),
  FOREIGN KEY (household_id) REFERENCES households(user_id) ON DELETE CASCADE,
  FOREIGN KEY (collector_id) REFERENCES collectors(user_id) ON DELETE SET NULL,
  FOREIGN KEY (recycler_id) REFERENCES recyclers(user_id) ON DELETE SET NULL
) ENGINE=InnoDB;

-- Requested materials of a request: one row per material, with its own weight, price and value.
CREATE TABLE request_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  category_id INT NOT NULL,
  est_weight DECIMAL(8,2) NOT NULL,           -- weight requested by the household
  price_per_kg DECIMAL(10,2) NOT NULL,        -- price applied when the request was booked
  est_value DECIMAL(10,2) NOT NULL,           -- est_weight x price_per_kg (calculated on the server)
  UNIQUE KEY uq_item (request_id, category_id),   -- a material can appear only once per request
  FOREIGN KEY (request_id) REFERENCES collection_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (category_id) REFERENCES waste_categories(id)
) ENGINE=InnoDB;

CREATE TABLE pickup_assignments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  collector_id INT NOT NULL,
  status ENUM('accepted','rejected') NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_assign (request_id, collector_id),
  FOREIGN KEY (request_id) REFERENCES collection_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (collector_id) REFERENCES collectors(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- One row per material actually weighed by the collector (matches request_items by request_id + category_id).
-- Doubles as the collector's stock lot for the marketplace.
CREATE TABLE waste_records (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL,
  collector_id INT NOT NULL,
  category_id INT NOT NULL,
  actual_weight DECIMAL(8,2) NOT NULL,
  price_per_kg DECIMAL(10,2) NOT NULL,
  final_value DECIMAL(10,2) NOT NULL,
  available_kg DECIMAL(8,2) NOT NULL,
  listed TINYINT(1) NOT NULL DEFAULT 0,
  ask_price_per_kg DECIMAL(10,2) NULL,
  recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_rec_item (request_id, category_id),
  INDEX (collector_id), INDEX (listed),
  FOREIGN KEY (request_id) REFERENCES collection_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (collector_id) REFERENCES collectors(user_id) ON DELETE CASCADE,
  FOREIGN KEY (category_id) REFERENCES waste_categories(id)
) ENGINE=InnoDB;

CREATE TABLE marketplace_orders (
  id INT AUTO_INCREMENT PRIMARY KEY,
  record_id INT NOT NULL,
  recycler_id INT NOT NULL,
  collector_id INT NOT NULL,
  quantity_kg DECIMAL(8,2) NOT NULL,
  offer_price_per_kg DECIMAL(10,2) NOT NULL,
  total DECIMAL(12,2) NOT NULL,
  status ENUM('requested','accepted','rejected','completed','cancelled') NOT NULL DEFAULT 'requested',
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (recycler_id), INDEX (collector_id),
  FOREIGN KEY (record_id) REFERENCES waste_records(id) ON DELETE CASCADE,
  FOREIGN KEY (recycler_id) REFERENCES recyclers(user_id) ON DELETE CASCADE,
  FOREIGN KEY (collector_id) REFERENCES collectors(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE transactions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  request_id INT NULL,
  order_id INT NULL,
  direction ENUM('credit','debit') NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  description VARCHAR(160) NOT NULL,
  reference VARCHAR(30) NOT NULL,
  status ENUM('success') NOT NULL DEFAULT 'success',   -- sandbox/simulated payments
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (user_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (request_id) REFERENCES collection_requests(id) ON DELETE SET NULL,
  FOREIGN KEY (order_id) REFERENCES marketplace_orders(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE notifications (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  message VARCHAR(255) NOT NULL,
  is_read TINYINT(1) NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX (user_id, is_read),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE feedback (
  id INT AUTO_INCREMENT PRIMARY KEY,
  request_id INT NOT NULL UNIQUE,
  household_id INT NOT NULL,
  collector_id INT NOT NULL,
  rating TINYINT NOT NULL,
  comment VARCHAR(500) NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (rating BETWEEN 1 AND 5),
  FOREIGN KEY (request_id) REFERENCES collection_requests(id) ON DELETE CASCADE,
  FOREIGN KEY (household_id) REFERENCES households(user_id) ON DELETE CASCADE,
  FOREIGN KEY (collector_id) REFERENCES collectors(user_id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ---------- Seed data ----------
INSERT INTO waste_categories (name, type, description, default_price) VALUES
('Paper','recyclable','Newspaper, office paper, magazines',12),
('Cardboard','recyclable','Boxes and cartons',8),
('Plastic (PET)','recyclable','Water and soft-drink bottles',18),
('Plastic (HDPE)','recyclable','Detergent bottles, buckets, containers',22),
('Glass','recyclable','Bottles and jars',3),
('Iron & Steel','recyclable','Tins, utensils, scrap iron',30),
('Aluminium','recyclable','Cans, foil, utensils',110),
('Copper','recyclable','Wires and copper scrap',520),
('E-waste','recyclable','Old phones, chargers, small electronics',40),
('Textiles','recyclable','Old clothes and fabric',5),
('Organic / Food Waste','biodegradable','Kitchen and garden waste (compostable)',0),
('Mixed Non-recyclable','non-recyclable','Sanitary items, thermocol, multilayer packaging',0);

-- Six months of price history (sample data; update waste_prices to change live prices)
INSERT INTO waste_prices (category_id, price_per_kg, effective_date)
SELECT c.id, ROUND(c.default_price * m.f, 2), DATE_SUB(CURDATE(), INTERVAL m.n MONTH)
FROM waste_categories c
JOIN (SELECT 5 AS n, 0.86 AS f UNION ALL SELECT 4,0.90 UNION ALL SELECT 3,0.95
      UNION ALL SELECT 2,0.93 UNION ALL SELECT 1,0.98 UNION ALL SELECT 0,1.00) m;
