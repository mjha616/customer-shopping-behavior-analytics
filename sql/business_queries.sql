-- PostgreSQL Enterprise Business Analytics & Queries Layer
-- This file contains advanced analytical queries addressing specific C-suite questions.

--------------------------------------------------------------------------------
-- 1. EXECUTIVE SUMMARY: Pareto (80/20) Revenue Concentration Analysis
-- Objective (CEO/CFO): Understand if a small segment of customers generates the bulk of revenue.
-- Tech Stack: CTEs, Window Functions (SUM, PERCENT_RANK)
--------------------------------------------------------------------------------
WITH customer_revenue AS (
    SELECT 
        customer_id,
        SUM(purchase_amount) AS total_customer_spend
    FROM fact_purchases
    GROUP BY customer_id
),
cumulative_revenue AS (
    SELECT 
        customer_id,
        total_customer_spend,
        SUM(total_customer_spend) OVER (ORDER BY total_customer_spend DESC) AS running_spend,
        SUM(total_customer_spend) OVER () AS total_company_revenue,
        PERCENT_RANK() OVER (ORDER BY total_customer_spend DESC) AS customer_percentile
    FROM customer_revenue
)
SELECT 
    CASE 
        WHEN customer_percentile <= 0.20 THEN 'Top 20% (High Value Customers)'
        WHEN customer_percentile <= 0.50 THEN 'Mid 20%-50% (Core Customers)'
        ELSE 'Bottom 50% (Long Tail Customers)'
    END AS customer_tier,
    COUNT(customer_id) AS total_customers,
    ROUND(SUM(total_customer_spend), 2) AS total_revenue_generated,
    ROUND(100.0 * SUM(total_customer_spend) / MAX(total_company_revenue), 2) AS revenue_contribution_pct,
    ROUND(AVG(total_customer_spend), 2) AS average_spend_per_customer
FROM cumulative_revenue
GROUP BY 
    CASE 
        WHEN customer_percentile <= 0.20 THEN 'Top 20% (High Value Customers)'
        WHEN customer_percentile <= 0.50 THEN 'Mid 20%-50% (Core Customers)'
        ELSE 'Bottom 50% (Long Tail Customers)'
    END
ORDER BY total_revenue_generated DESC;


--------------------------------------------------------------------------------
-- 2. CUSTOMER STRATEGY: RFM (Recency, Frequency, Monetary) Customer Segmentation
-- Objective (VP Marketing): Categorize customers into actionable behavioral segments.
-- Note: Recency is proxied by purchase_frequency_days (shorter days = more recent/active).
-- Tech Stack: CTEs, NTILE Window Functions, CASE Statements
--------------------------------------------------------------------------------
WITH rfm_raw AS (
    SELECT 
        c.customer_id,
        c.gender,
        -- Lower purchase frequency days implies the customer purchases more frequently (Recency Proxy)
        f.purchase_frequency_days AS recency_proxy,
        f.previous_purchases AS frequency,
        f.estimated_clv AS monetary
    FROM dim_customers c
    JOIN fact_purchases f ON c.customer_id = f.customer_id
),
rfm_scores AS (
    SELECT 
        customer_id,
        gender,
        -- Score Recency (1 = low frequency, 4 = high frequency/weekly)
        NTILE(4) OVER (ORDER BY recency_proxy ASC) AS r_score, 
        -- Score Frequency (1 = low previous purchases, 4 = high previous purchases)
        NTILE(4) OVER (ORDER BY frequency ASC) AS f_score,
        -- Score Monetary (1 = low CLV, 4 = high CLV)
        NTILE(4) OVER (ORDER BY monetary ASC) AS m_score
    FROM rfm_raw
),
rfm_segments AS (
    SELECT 
        customer_id,
        gender,
        r_score,
        f_score,
        m_score,
        (r_score * 100 + f_score * 10 + m_score) AS rfm_combined_code,
        CASE
            WHEN r_score >= 3 AND f_score >= 3 AND m_score >= 3 THEN 'Champions (Frequent, Loyal, High Spend)'
            WHEN r_score >= 2 AND f_score >= 3 AND m_score >= 2 THEN 'Loyal Customers (Regular, Moderate-High Spend)'
            WHEN r_score >= 3 AND f_score <= 2 THEN 'Promising (Recent Buyers, Low Frequency)'
            WHEN r_score <= 2 AND f_score >= 3 AND m_score >= 3 THEN 'At-Risk Value (High Value but Inactive)'
            WHEN r_score <= 1 AND f_score <= 2 THEN 'Lost Customers (Infrequent, Low Spend)'
            ELSE 'Needs Attention (Average/Mixed Profile)'
        END AS customer_segment
    FROM rfm_scores
)
SELECT 
    customer_segment,
    COUNT(customer_id) AS customer_count,
    ROUND(100.0 * COUNT(customer_id) / SUM(COUNT(customer_id)) OVER (), 2) AS customer_share_pct,
    SUM(CASE WHEN gender = 'Male' THEN 1 ELSE 0 END) AS male_count,
    SUM(CASE WHEN gender = 'Female' THEN 1 ELSE 0 END) AS female_count
FROM rfm_segments
GROUP BY customer_segment
ORDER BY customer_count DESC;


--------------------------------------------------------------------------------
-- 3. PRODUCT & MERCHANDISING: Seasonal Category Rank Shifting & Trend Velocity
-- Objective (VP Product): Track which categories dominate in which seasons and rank momentum.
-- Tech Stack: CTEs, ROW_NUMBER/DENSE_RANK, LAG Window Functions
--------------------------------------------------------------------------------
WITH seasonal_revenue AS (
    SELECT 
        f.season,
        p.category,
        SUM(f.purchase_amount) AS category_revenue,
        COUNT(f.purchase_id) AS volume_sold
    FROM fact_purchases f
    JOIN dim_products p ON f.product_id = p.product_id
    GROUP BY f.season, p.category
),
seasonal_ranking AS (
    SELECT 
        season,
        category,
        category_revenue,
        volume_sold,
        DENSE_RANK() OVER (PARTITION BY season ORDER BY category_revenue DESC) AS seasonal_rank
    FROM seasonal_revenue
),
ordered_seasons AS (
    SELECT 
        season,
        category,
        category_revenue,
        seasonal_rank,
        -- Map seasons to chronological order for lag tracking
        CASE 
            WHEN season = 'Spring' THEN 1
            WHEN season = 'Summer' THEN 2
            WHEN season = 'Fall' THEN 3
            WHEN season = 'Winter' THEN 4
        END AS season_order
    FROM seasonal_ranking
),
seasonal_shift AS (
    SELECT 
        o1.season,
        o1.category,
        o1.category_revenue,
        o1.seasonal_rank,
        LAG(o1.seasonal_rank) OVER (PARTITION BY o1.category ORDER BY o1.season_order) AS previous_season_rank,
        LAG(o1.category_revenue) OVER (PARTITION BY o1.category ORDER BY o1.season_order) AS previous_season_revenue
    FROM ordered_seasons o1
)
SELECT 
    season,
    category,
    category_revenue AS current_season_revenue,
    seasonal_rank,
    COALESCE(previous_season_rank::TEXT, 'N/A (First Season)') AS previous_season_rank,
    CASE 
        WHEN previous_season_rank IS NULL THEN 'N/A'
        WHEN seasonal_rank < previous_season_rank THEN 'Rank Up (Growth Trend)'
        WHEN seasonal_rank > previous_season_rank THEN 'Rank Down (Decline Trend)'
        ELSE 'Neutral'
    END AS rank_momentum,
    ROUND(COALESCE(category_revenue - previous_season_revenue, 0), 2) AS revenue_variance
FROM seasonal_shift
ORDER BY season, seasonal_rank;


--------------------------------------------------------------------------------
-- 4. MERCHANDISING: Size and Color Seasonal Demand Heatmap
-- Objective (VP Product): Provide operational guidance on what colors and sizes to purchase.
-- Tech Stack: Pivot-like aggregations using SUM(CASE), Grouping Sets
--------------------------------------------------------------------------------
SELECT 
    category,
    size,
    COUNT(purchase_id) AS units_sold,
    ROUND(SUM(purchase_amount), 2) AS total_sales,
    -- Top performing color for this size
    (
        SELECT color 
        FROM fact_purchases f2 
        JOIN dim_products p2 ON f2.product_id = p2.product_id 
        WHERE p2.category = dp.category AND f2.size = fp.size 
        GROUP BY color 
        ORDER BY COUNT(f2.purchase_id) DESC 
        LIMIT 1
    ) AS top_performing_color
FROM fact_purchases fp
JOIN dim_products dp ON fp.product_id = dp.product_id
GROUP BY category, size
ORDER BY category, total_sales DESC;


--------------------------------------------------------------------------------
-- 5. FINANCE: Promo Code Efficiency & NPS Cannibalization Matrix
-- Objective (CFO): Evaluate if discounts are driving ratings or cannibalizing revenue.
-- Note: Net Promoter Score (NPS) proxy is calculated as: (% Promoters - % Detractors)
-- Tech Stack: Multi-level aggregation, CASE conditional counts
--------------------------------------------------------------------------------
SELECT 
    f.loyalty_segment,
    f.discount_applied,
    COUNT(f.purchase_id) AS total_orders,
    ROUND(AVG(f.purchase_amount), 2) AS average_order_value (AOV),
    ROUND(SUM(f.purchase_amount), 2) AS total_revenue,
    -- NPS Score Calculation: Promoters (rating >= 4) - Detractors (rating <= 3) / Total
    ROUND(
        100.0 * (
            SUM(CASE WHEN f.review_rating >= 4.0 THEN 1 ELSE 0 END) - 
            SUM(CASE WHEN f.review_rating <= 3.0 THEN 1 ELSE 0 END)
        ) / COUNT(f.purchase_id),
        2
    ) AS nps_proxy_pct
FROM fact_purchases f
GROUP BY f.loyalty_segment, f.discount_applied
ORDER BY f.loyalty_segment, f.discount_applied;


--------------------------------------------------------------------------------
-- 6. OPERATIONS: Shipping Method Economics & Customer Retention Impact
-- Objective (VP Operations): See if premium shipping methods justify operational costs.
-- Tech Stack: Average Rating checks, Customer Segment distribution
--------------------------------------------------------------------------------
SELECT 
    ds.shipping_type,
    COUNT(fp.purchase_id) AS shipment_volume,
    ROUND(AVG(fp.purchase_amount), 2) AS average_order_value,
    ROUND(AVG(fp.review_rating), 2) AS average_customer_rating,
    ROUND(100.0 * SUM(CASE WHEN dc.subscription_status = TRUE THEN 1 ELSE 0 END) / COUNT(fp.purchase_id), 2) AS subscriber_ratio_pct
FROM fact_purchases fp
JOIN dim_shipping ds ON fp.shipping_id = ds.shipping_id
JOIN dim_customers dc ON fp.customer_id = dc.customer_id
GROUP BY ds.shipping_type
ORDER BY shipment_volume DESC;
