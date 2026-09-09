#!/bin/zsh
set -e
cd -- "${0:A:h}"
if [[ ! -f data/housing.sqlite ]]; then
  python3 build_data.py
fi
print '杭州住房 GIS：http://127.0.0.1:8766'
print '保持此终端窗口运行；关闭窗口会停止本地服务。'
python3 server.py --port 8766
