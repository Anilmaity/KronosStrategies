-- HARD-DELETE retired strategies and ALL downstream data (2026-05-18)
--   • VAR1  (Liquidity Scalper)   — unprofitable
--   • S6    (ICT Daily CRT D1)    — sample too small
--
-- FK chain has ondelete=CASCADE end-to-end, so deleting the apis_strategy
-- row cascades through user_strategy → position → {trigger, order} plus
-- signal and action rows attached to the strategy.
--
-- DESTRUCTIVE. Wipes order history. Run only after the SELECTs match expectations.
-- Prefer running via db/retire_unprofitable_2026_05_18.py --commit, which
-- prints counts before delete.

BEGIN;

-- 1. Inspect what will go away (counts)
WITH targets AS (
    SELECT id FROM apis_strategy
    WHERE  name IN ('Liquidity Scalper VAR1', 'ICT Daily CRT (D1)')
)
SELECT
    (SELECT COUNT(*) FROM apis_strategy WHERE id IN (SELECT id FROM targets))     AS strategy,
    (SELECT COUNT(*) FROM apis_userstrategy WHERE strategy_id IN (SELECT id FROM targets)) AS user_strategy,
    (SELECT COUNT(*) FROM apis_position WHERE user_strategy_id IN
        (SELECT id FROM apis_userstrategy WHERE strategy_id IN (SELECT id FROM targets))) AS position,
    (SELECT COUNT(*) FROM apis_order WHERE position_id IN
        (SELECT id FROM apis_position WHERE user_strategy_id IN
            (SELECT id FROM apis_userstrategy WHERE strategy_id IN (SELECT id FROM targets)))) AS "order",
    (SELECT COUNT(*) FROM apis_trigger WHERE position_id IN
        (SELECT id FROM apis_position WHERE user_strategy_id IN
            (SELECT id FROM apis_userstrategy WHERE strategy_id IN (SELECT id FROM targets)))) AS trigger,
    (SELECT COUNT(*) FROM apis_signal WHERE strategy_id IN (SELECT id FROM targets)) AS signal,
    (SELECT COUNT(*) FROM apis_action WHERE strategy_id IN (SELECT id FROM targets)) AS action;

-- 2. Cascade delete the Strategy rows. Everything downstream goes with them.
DELETE FROM apis_strategy
WHERE name IN ('Liquidity Scalper VAR1', 'ICT Daily CRT (D1)');

-- 3. Verify zero rows remain
SELECT name FROM apis_strategy
WHERE name IN ('Liquidity Scalper VAR1', 'ICT Daily CRT (D1)');
-- Expected: 0 rows.

-- If correct: COMMIT;   else: ROLLBACK;
-- COMMIT;
