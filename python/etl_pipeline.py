import os
import sys
import logging
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

# Setup logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('etl_pipeline.log', mode='w')
    ]
)
logger = logging.getLogger(__name__)

# Configuration
RAW_DATA_PATH = os.path.join("data", "raw", "customer_shopping_behavior.csv")
PROCESSED_DIR = os.path.join("data", "processed")

# DB Credentials (defaults to local config; can be overridden by environment variables)
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Manas%40121")  # URL-encoded @ is %40
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "customer_behaviour")

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

def bin_age(age):
    """Bins age into standard business demographics."""
    if age <= 25:
        return 'Young Adult'
    elif age <= 40:
        return 'Adult'
    elif age <= 60:
        return 'Middle-aged'
    else:
        return 'Senior'

def clean_and_validate_data(df):
    """Cleans data and validates values against strict schema rules."""
    logger.info("Starting data cleaning and schema validation...")
    
    # 1. Standardize column names (snake_case)
    df.columns = df.columns.str.strip().str.lower().str.replace(' ', '_').str.replace('(', '').str.replace(')', '')
    df = df.rename(columns={'purchase_amount_usd': 'purchase_amount'})
    
    # 2. Track and impute missing ratings
    df['review_rating_imputed'] = df['review_rating'].isnull()
    missing_ratings_count = df['review_rating_imputed'].sum()
    logger.info(f"Found {missing_ratings_count} missing review ratings. Applying category median imputation.")
    
    # Impute missing values using category-specific medians
    category_medians = df.groupby('category')['review_rating'].transform('median')
    df['review_rating'] = df['review_rating'].fillna(category_medians)
    
    # Standard fallback if any category is completely null (though not present here)
    df['review_rating'] = df['review_rating'].fillna(df['review_rating'].median())
    
    # 3. Handle promo code and discount alignment
    # As identified in the audit, discount_applied and promo_code_used are 100% correlated.
    # We drop promo_code_used and cast discount_applied to boolean.
    if 'promo_code_used' in df.columns:
        df = df.drop(columns=['promo_code_used'])
    
    df['discount_applied'] = df['discount_applied'].map({'Yes': True, 'No': False})
    df['subscription_status'] = df['subscription_status'].map({'Yes': True, 'No': False})
    
    # 4. Strict Schema and Business Rule Validation
    logger.info("Executing row-by-row structural validation constraints...")
    
    # Age validation (18 to 120)
    invalid_age = df[(df['age'] < 18) | (df['age'] > 120)]
    if not invalid_age.empty:
        logger.warning(f"Validation Error: Found {len(invalid_age)} records with ages outside [18, 120].")
        
    # Purchase Amount validation (> 0)
    invalid_spend = df[df['purchase_amount'] <= 0]
    if not invalid_spend.empty:
        raise ValueError(f"Data Validation Failure: {len(invalid_spend)} records found with non-positive purchase amounts.")
        
    # Rating validation (1.0 to 5.0)
    invalid_rating = df[(df['review_rating'] < 1.0) | (df['review_rating'] > 5.0)]
    if not invalid_rating.empty:
        raise ValueError(f"Data Validation Failure: {len(invalid_rating)} records found with ratings outside [1.0, 5.0].")
    
    # Check for duplicate Customer IDs
    duplicate_customers = df['customer_id'].duplicated().sum()
    if duplicate_customers > 0:
        logger.warning(f"Found {duplicate_customers} duplicate customer ID records. Deduplicating.")
        df = df.drop_duplicates(subset=['customer_id'])
        
    logger.info("Data cleaning and validation completed successfully.")
    return df

def perform_feature_engineering(df):
    """Engineers business metrics and derived fields based on approved plan."""
    logger.info("Performing business feature engineering...")
    
    # 1. Age Group Binning
    df['age_group'] = df['age'].apply(bin_age)
    
    # 2. Purchase Frequency Days Map
    frequency_mapping = {
        'Weekly': 7,
        'Bi-Weekly': 14,
        'Fortnightly': 14,
        'Monthly': 30,
        'Quarterly': 90,
        'Every 3 Months': 90,
        'Annually': 365
    }
    df['purchase_frequency_days'] = df['frequency_of_purchases'].map(frequency_mapping)
    # Default fallback for unmapped frequency (if any)
    df['purchase_frequency_days'] = df['purchase_frequency_days'].fillna(30).astype(int)
    
    # 3. Estimated Customer Lifetime Value (CLV Proxy)
    # Formula: Current Order Value * (Previous Purchases + 1)
    df['estimated_clv'] = df['purchase_amount'] * (df['previous_purchases'] + 1)
    
    # 4. Estimated Annual Spend
    df['estimated_annual_spend'] = df['purchase_amount'] * (365 / df['purchase_frequency_days'])
    
    # 5. Customer Sentiment Classification
    df['sentiment_class'] = pd.cut(
        df['review_rating'],
        bins=[0.0, 3.0, 4.0, 5.0],
        labels=['Detractor', 'Passive', 'Promoter']
    ).astype(str)
    
    # 6. Customer Loyalty Segments based on Previous Purchases
    df['loyalty_segment'] = pd.cut(
        df['previous_purchases'],
        bins=[-1, 4, 14, 29, 100],
        labels=['New', 'Regular', 'Loyal', 'Brand Advocate']
    ).astype(str)
    
    logger.info("Feature engineering finished successfully.")
    return df

def normalize_to_snowflake_schema(df):
    """Splits flat dataframe into normalized dimension and fact tables."""
    logger.info("Normalizing data to Snowflake Schema...")
    
    # Ensure processed directory exists
    os.makedirs(PROCESSED_DIR, exist_ok=True)
    
    # 1. dim_customers
    dim_customers = df[['customer_id', 'age', 'age_group', 'gender', 'subscription_status']].drop_duplicates().copy()
    
    # 2. dim_products
    # We define unique products as distinct item names and categories.
    dim_products_raw = df[['item_purchased', 'category']].drop_duplicates().reset_index(drop=True)
    dim_products_raw['product_id'] = dim_products_raw.index + 1
    dim_products = dim_products_raw.rename(columns={'item_purchased': 'item_name'})
    dim_products = dim_products[['product_id', 'item_name', 'category']]
    
    # 3. dim_locations
    dim_locations_raw = df[['location']].drop_duplicates().reset_index(drop=True)
    dim_locations_raw['location_id'] = dim_locations_raw.index + 1
    dim_locations = dim_locations_raw.rename(columns={'location': 'location_name'})
    dim_locations = dim_locations[['location_id', 'location_name']]
    
    # 4. dim_shipping
    dim_shipping_raw = df[['shipping_type']].drop_duplicates().reset_index(drop=True)
    dim_shipping_raw['shipping_id'] = dim_shipping_raw.index + 1
    dim_shipping = dim_shipping_raw.copy()
    dim_shipping = dim_shipping[['shipping_id', 'shipping_type']]
    
    # 5. dim_payment
    dim_payment_raw = df[['payment_method']].drop_duplicates().reset_index(drop=True)
    dim_payment_raw['payment_id'] = dim_payment_raw.index + 1
    dim_payment = dim_payment_raw.copy()
    dim_payment = dim_payment[['payment_id', 'payment_method']]
    
    # 6. fact_purchases
    # Merge dimension IDs back to base dataframe
    fact_temp = df.merge(dim_products_raw, on=['item_purchased', 'category'], how='left')
    fact_temp = fact_temp.merge(dim_locations_raw, on='location', how='left')
    fact_temp = fact_temp.merge(dim_shipping_raw, on='shipping_type', how='left')
    fact_temp = fact_temp.merge(dim_payment_raw, on='payment_method', how='left')
    
    # Generate PK for fact table (row identifier)
    fact_temp['purchase_id'] = fact_temp.index + 1
    
    fact_purchases = fact_temp[[
        'purchase_id', 'customer_id', 'product_id', 'location_id', 'shipping_id', 'payment_id',
        'size', 'color', 'season', 'review_rating', 'review_rating_imputed', 'discount_applied',
        'purchase_amount', 'previous_purchases', 'purchase_frequency_days', 'estimated_clv',
        'estimated_annual_spend', 'sentiment_class', 'loyalty_segment'
    ]].copy()
    
    # Export processed data to local CSVs
    dim_customers.to_csv(os.path.join(PROCESSED_DIR, "dim_customers.csv"), index=False)
    dim_products.to_csv(os.path.join(PROCESSED_DIR, "dim_products.csv"), index=False)
    dim_locations.to_csv(os.path.join(PROCESSED_DIR, "dim_locations.csv"), index=False)
    dim_shipping.to_csv(os.path.join(PROCESSED_DIR, "dim_shipping.csv"), index=False)
    dim_payment.to_csv(os.path.join(PROCESSED_DIR, "dim_payment.csv"), index=False)
    fact_purchases.to_csv(os.path.join(PROCESSED_DIR, "fact_purchases.csv"), index=False)
    
    logger.info(f"Successfully normalized and exported files to '{PROCESSED_DIR}'.")
    return dim_customers, dim_products, dim_locations, dim_shipping, dim_payment, fact_purchases

def load_to_postgresql(tables):
    """Loads dimension and fact dataframes into PostgreSQL database."""
    dim_customers, dim_products, dim_locations, dim_shipping, dim_payment, fact_purchases = tables
    
    logger.info(f"Attempting connection to PostgreSQL database at {DB_HOST}:{DB_PORT}/{DB_NAME}...")
    try:
        engine = create_engine(DATABASE_URL)
        with engine.begin() as conn:
            # We recreate tables and enforce structure using SQLAlchemy
            logger.info("Dropping existing tables if they exist to enforce clean schema...")
            conn.execute(text("DROP TABLE IF EXISTS fact_purchases CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS dim_customers CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS dim_products CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS dim_locations CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS dim_shipping CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS dim_payment CASCADE;"))
            
            logger.info("Generating schema tables in PostgreSQL...")
            
            # Create DDL tables
            conn.execute(text("""
                CREATE TABLE dim_customers (
                    customer_id INT PRIMARY KEY,
                    age INT NOT NULL CHECK (age >= 18),
                    age_group VARCHAR(50) NOT NULL,
                    gender VARCHAR(20) NOT NULL,
                    subscription_status BOOLEAN NOT NULL
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE dim_products (
                    product_id INT PRIMARY KEY,
                    item_name VARCHAR(100) NOT NULL,
                    category VARCHAR(100) NOT NULL
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE dim_locations (
                    location_id INT PRIMARY KEY,
                    location_name VARCHAR(100) NOT NULL
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE dim_shipping (
                    shipping_id INT PRIMARY KEY,
                    shipping_type VARCHAR(100) NOT NULL
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE dim_payment (
                    payment_id INT PRIMARY KEY,
                    payment_method VARCHAR(100) NOT NULL
                );
            """))
            
            conn.execute(text("""
                CREATE TABLE fact_purchases (
                    purchase_id INT PRIMARY KEY,
                    customer_id INT REFERENCES dim_customers(customer_id),
                    product_id INT REFERENCES dim_products(product_id),
                    location_id INT REFERENCES dim_locations(location_id),
                    shipping_id INT REFERENCES dim_shipping(shipping_id),
                    payment_id INT REFERENCES dim_payment(payment_id),
                    size VARCHAR(10) NOT NULL,
                    color VARCHAR(50) NOT NULL,
                    season VARCHAR(20) NOT NULL,
                    review_rating NUMERIC(3, 2) NOT NULL CHECK (review_rating >= 1.0 AND review_rating <= 5.0),
                    review_rating_imputed BOOLEAN NOT NULL DEFAULT FALSE,
                    discount_applied BOOLEAN NOT NULL DEFAULT FALSE,
                    purchase_amount NUMERIC(10, 2) NOT NULL CHECK (purchase_amount > 0),
                    previous_purchases INT NOT NULL DEFAULT 0,
                    purchase_frequency_days INT NOT NULL,
                    estimated_clv NUMERIC(12, 2) NOT NULL,
                    estimated_annual_spend NUMERIC(12, 2) NOT NULL,
                    sentiment_class VARCHAR(20) NOT NULL,
                    loyalty_segment VARCHAR(50) NOT NULL
                );
            """))
            
            # Setup Performance Indexes
            logger.info("Creating optimization indexes...")
            conn.execute(text("CREATE INDEX idx_fact_customer_id ON fact_purchases(customer_id);"))
            conn.execute(text("CREATE INDEX idx_fact_product_id ON fact_purchases(product_id);"))
            conn.execute(text("CREATE INDEX idx_fact_location_id ON fact_purchases(location_id);"))
            conn.execute(text("CREATE INDEX idx_fact_purchase_amount ON fact_purchases(purchase_amount);"))
            conn.execute(text("CREATE INDEX idx_dim_products_category ON dim_products(category);"))
            
        logger.info("PostgreSQL Database tables created successfully.")
        
        # Load tables using pandas to_sql with if_exists="append"
        logger.info("Inserting processed data into PostgreSQL tables...")
        dim_customers.to_sql('dim_customers', engine, if_exists='append', index=False)
        dim_products.to_sql('dim_products', engine, if_exists='append', index=False)
        dim_locations.to_sql('dim_locations', engine, if_exists='append', index=False)
        dim_shipping.to_sql('dim_shipping', engine, if_exists='append', index=False)
        dim_payment.to_sql('dim_payment', engine, if_exists='append', index=False)
        fact_purchases.to_sql('fact_purchases', engine, if_exists='append', index=False)
        
        logger.info("Data loaded to PostgreSQL successfully.")
        
    except Exception as e:
        logger.error(f"Error during loading to PostgreSQL: {e}")
        logger.warning("Pipeline proceeding despite database connection issue. Check credentials and if PostgreSQL is running.")
        raise e

def main():
    logger.info("Starting Enterprise Customer Behavior ETL Pipeline...")
    
    # Verify raw file exists
    if not os.path.exists(RAW_DATA_PATH):
        logger.error(f"Raw data file not found at {RAW_DATA_PATH}. Exiting pipeline.")
        sys.exit(1)
        
    # Extraction
    raw_df = pd.read_csv(RAW_DATA_PATH)
    logger.info(f"Extracted {len(raw_df)} rows from raw CSV.")
    
    # Cleaning & Validation
    clean_df = clean_and_validate_data(raw_df)
    
    # Feature Engineering
    engineered_df = perform_feature_engineering(clean_df)
    
    # Normalization & CSV Output
    tables = normalize_to_snowflake_schema(engineered_df)
    
    # Loading to PostgreSQL
    try:
        load_to_postgresql(tables)
        logger.info("ETL pipeline executed successfully! Database and files are fully updated.")
    except Exception:
        logger.warning("Database write failed, but CSV dimensions and facts were exported successfully to data/processed/")
        
if __name__ == "__main__":
    main()
