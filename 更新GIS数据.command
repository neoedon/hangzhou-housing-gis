#!/bin/zsh
set -e
cd -- "${0:A:h}"
python3 build_data.py
print '更新通过。请刷新 GIS 网页，使所有面板使用同一份数据。'
