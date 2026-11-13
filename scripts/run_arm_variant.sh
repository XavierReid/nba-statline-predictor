#!/bin/sh
# Build, audit and evaluate (10 seeds) the P/D/O_r arms of one variant for one season.
#   scripts/run_arm_variant.sh 2016-17 m3b
S=$1; V=$2; U=$(echo $S | tr - _); mkdir -p /tmp/arm_logs
for a in P D O_r; do scripts/vrun nba_backfill validation.ratings.e2e.arms $S ${a}_$V > /tmp/arm_logs/build_${a}_${V}_$S.log 2>&1 || echo "BUILD FAIL $a $S"; done
for a in P D O_r; do l=$(echo ${a}_$V | tr A-Z a-z); scripts/vrun nba_arm_${l}_$U validation.ratings.e2e.audit_arm $S ${a}_$V > /tmp/arm_logs/audit_${a}_${V}_$S.log 2>&1 || echo "AUDIT FAIL $a $S"; done
for a in P D O_r; do l=$(echo ${a}_$V | tr A-Z a-z); scripts/vrun nba_arm_${l}_$U validation.ratings.e2e.evaluate $S ${a}_$V 10 > /tmp/arm_logs/eval_${a}_${V}_$S.log 2>&1 & done
wait
echo "done $S $V"
