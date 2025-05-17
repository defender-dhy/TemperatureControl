# ble_lv_control_test.py

import time
from ble_lv_control import (
    start_ble_service,
    read_log,
    stop_ble_service
)

if __name__ == "__main__":
    # 请根据你的实际情况修改以下字符串
    SERVICE_UUID       = "00001234-0000-1000-8000-00805F9B34FB"
    TX_CHAR_UUID       = "00005678-0000-1000-8000-00805F9B34FB"
    RX_CHAR_UUID       = "0000ABCD-0000-1000-8000-00805F9B34FB"
    VI_PATH            = r"E:\_team_proj\temp_control\my_labview\my_labview_projs\PC_lv_bluetooth\bt_test2.vi"

    print("=== 启动 BLE 服务 ===")
    ok = start_ble_service(
        SERVICE_UUID,
        TX_CHAR_UUID,
        RX_CHAR_UUID,
        VI_PATH
    )
    print("start_ble_service returned:", ok)

    print("\n--- 等待 5 秒，让服务有时间启动并写日志 ---")
    time.sleep(5)

    print("\n=== 初次读取日志 ===")
    log = read_log()
    print(log)

    print("=== 停止 BLE 服务 ===")
    ok2 = stop_ble_service()
    print("stop_ble_service returned:", ok2)

    print("\n--- 再次读取日志 ---")
    log2 = read_log()
    print(log2)

    print("=== 测试结束 ===")
