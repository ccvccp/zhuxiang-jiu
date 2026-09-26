#!/bin/bash
# P0-b: 清空 36 号进化层 mock 学习数据(Qwen 联合检测建议)
set -e
R=zhuxiang-redis-1
echo "== before =="
docker exec $R redis-cli --scan --pattern 'zhuxiang:promo:*evo*' | wc -l
for k in $(docker exec $R redis-cli --scan --pattern 'zhuxiang:promo:*evo*'); do
  docker exec $R redis-cli DEL "$k" > /dev/null
done
echo "== after =="
docker exec $R redis-cli --scan --pattern 'zhuxiang:promo:*evo*' | wc -l
