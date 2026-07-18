-- PostgreSQL Enterprise Schema DDL & Optimization Layer
-- Snowflake schema for Customer Shopping Behavior Analysis

-- Drop tables in reverse dependency order to prevent constraint violations
DROP TABLE IF EXISTS fact_purchases CASCADE;
DROP TABLE IF EXISTS dim_customers CASCADE;
DROP TABLE IF EXISTS dim_products CASCADE;
DROP TABLE IF EXISTS dim_locations CASCADE;
DROP TABLE IF EXISTS dim_shipping CASCADE;
DROP TABLE IF EXISTS dim_payment CASCADE;

-- 1. Dimension: Customers
CREATE TABLE dim_customers (
    customer_id INT PRIMARY KEY,
    age INT NOT NULL CHECK (age >= 18 AND age <= 120),
    age_group VARCHAR(50) NOT NULL,
    gender VARCHAR(20) NOT NULL CHECK (gender IN ('Male', 'Female', 'Non-Binary', 'Other')),
    subscription_status BOOLEAN NOT NULL DEFAULT FALSE
);

-- 2. Dimension: Products Catalog
CREATE TABLE dim_products (
    product_id INT PRIMARY KEY,
    item_name VARCHAR(100) NOT NULL,
    category VARCHAR(100) NOT NULL
);

-- 3. Dimension: Locations
CREATE TABLE dim_locations (
    location_id INT PRIMARY KEY,
    location_name VARCHAR(100) NOT NULL UNIQUE
);

-- 4. Dimension: Shipping Methods
CREATE TABLE dim_shipping (
    shipping_id INT PRIMARY KEY,
    shipping_type VARCHAR(100) NOT NULL UNIQUE
);

-- 5. Dimension: Payment Methods
CREATE TABLE dim_payment (
    payment_id INT PRIMARY KEY,
    payment_method VARCHAR(100) NOT NULL UNIQUE
);

-- 6. Central Fact Table: Purchases
CREATE TABLE fact_purchases (
    purchase_id INT PRIMARY KEY,
    customer_id INT REFERENCES dim_customers(customer_id) ON DELETE CASCADE,
    product_id INT REFERENCES dim_products(product_id) ON DELETE RESTRICT,
    location_id INT REFERENCES dim_locations(location_id) ON DELETE RESTRICT,
    shipping_id INT REFERENCES dim_shipping(shipping_id) ON DELETE RESTRICT,
    payment_id INT REFERENCES dim_payment(payment_id) ON DELETE RESTRICT,
    size VARCHAR(10) NOT NULL,
    color VARCHAR(50) NOT NULL,
    season VARCHAR(20) NOT NULL CHECK (season IN ('Spring', 'Summer', 'Fall', 'Winter')),
    review_rating NUMERIC(3, 2) NOT NULL CHECK (review_rating >= 1.0 AND review_rating <= 5.0),
    review_rating_imputed BOOLEAN NOT NULL DEFAULT FALSE,
    discount_applied BOOLEAN NOT NULL DEFAULT FALSE,
    purchase_amount NUMERIC(10, 2) NOT NULL CHECK (purchase_amount > 0),
    previous_purchases INT NOT NULL DEFAULT 0 CHECK (previous_purchases >= 0),
    purchase_frequency_days INT NOT NULL CHECK (purchase_frequency_days > 0),
    estimated_clv NUMERIC(12, 2) NOT NULL CHECK (estimated_clv >= 0),
    estimated_annual_spend NUMERIC(12, 2) NOT NULL CHECK (estimated_annual_spend >= 0),
    sentiment_class VARCHAR(20) NOT NULL CHECK (sentiment_class IN ('Detractor', 'Passive', 'Promoter')),
    loyalty_segment VARCHAR(50) NOT NULL CHECK (loyalty_segment IN ('New', 'Regular', 'Loyal', 'Brand Advocate'))
);

-- Optimization & Performance Indexing Strategy
-- Create indexes on Foreign Keys to optimize JOIN performance
CREATE INDEX idx_fact_customer_id ON fact_purchases(customer_id);
CREATE INDEX idx_fact_product_id ON fact_purchases(product_id);
CREATE INDEX idx_fact_location_id ON fact_purchases(location_id);
CREATE INDEX idx_fact_shipping_id ON fact_purchases(shipping_id);
CREATE INDEX idx_fact_payment_id ON fact_purchases(payment_id);

-- Create compound index for seasonal filter queries
CREATE INDEX idx_fact_season_amount ON fact_purchases(season, purchase_amount);

-- Create B-Tree index on high-frequency search fields
CREATE INDEX idx_dim_products_category ON dim_products(category);
CREATE INDEX idx_dim_customers_loyalty ON fact_purchases(loyalty_segment);
