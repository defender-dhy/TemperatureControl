import asyncio
import sys
import uuid
import logging

from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattServiceProvider,
    GattLocalCharacteristicParameters,
    GattServiceProviderAdvertisingParameters,
    GattCharacteristicProperties,
)
from winrt.windows.storage.streams import DataWriter
from winrt.windows.storage.streams import DataReader

# ── 基本配置 ───────────────────────────────────────────────────
SERVICE_UUID = uuid.UUID("00001234-0000-1000-8000-00805F9B34FB")
TX_CHAR_UUID = uuid.UUID("00005678-0000-1000-8000-00805F9B34FB")
RX_CHAR_UUID = uuid.UUID("0000ABCD-0000-1000-8000-00805F9B34FB")   # iPhone → PC
# ───────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")

async def setup_gatt() -> GattServiceProvider:
    logging.info("创建 GATT Service …")
    res = await GattServiceProvider.create_async(SERVICE_UUID)
    if res.error != 0:
        logging.error(f"GATT Service 创建失败，错误码 {res.error}")
        sys.exit(1)

    provider = res.service_provider
    svc      = provider.service

    # 记录主事件循环
    loop = asyncio.get_running_loop()

    # ── 1. 原有 READ/NOTIFY 特征 ─────────────────────
    rd = GattLocalCharacteristicParameters()
    rd.characteristic_properties = (
        GattCharacteristicProperties.READ | GattCharacteristicProperties.NOTIFY
    )
    rd.user_description = "PC to iPhone TX characteristic"
    w = DataWriter(); w.write_string("Hello from Python!")
    rd.static_value = w.detach_buffer()
    await svc.create_characteristic_async(TX_CHAR_UUID, rd)

    # ── 2. 新增 WRITE / WRITE_WITHOUT_RESPONSE 特征 ──
    wr = GattLocalCharacteristicParameters()
    wr.characteristic_properties = (
        GattCharacteristicProperties.WRITE |
        GattCharacteristicProperties.WRITE_WITHOUT_RESPONSE
    )
    wr.user_description = "IPhone to PC RX characteristic"

    rx_result = await svc.create_characteristic_async(RX_CHAR_UUID, wr)
    if rx_result.error != 0:
        logging.error(f"RX Characteristic 创建失败，错误码 {rx_result.error}")
        sys.exit(1)

    rx_char = rx_result.characteristic

    def on_write(sender, args):
        op = args.get_request_async()
        def completed(async_info, async_status):
            # 同步拿到写请求
            req = async_info.get_results()
            buffer = req.value  # IBuffer

            # 用 DataReader 从 IBuffer 中逐字节读出数据
            reader = DataReader.from_buffer(buffer)
            data_list = []
            # unconsumed_buffer_length 表示还没读的字节数
            while reader.unconsumed_buffer_length > 0:
                # 逐个读出 uint8
                b = reader.read_byte()
                data_list.append(b)
            data = bytes(data_list)

            logging.info(f"📥 收到 iPhone 写入: {data.hex()} ({data!r})")
            # req.respond()  # 确认写入成功

        op.completed = completed

    rx_char.add_write_requested(on_write)   

    logging.info("✅ 两个特征 (R/N, W) 均已注册")
    return provider


def adv_params() -> GattServiceProviderAdvertisingParameters:
    """只设置 connectable / discoverable——兼容所有 Win10 版本"""
    par = GattServiceProviderAdvertisingParameters()
    par.is_connectable  = True
    par.is_discoverable = True
    return par

async def main():
    provider = await setup_gatt()
    provider.start_advertising(adv_params())
    logging.info("广播已启动，设备处于可连接状态（Ctrl-C 结束）")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        provider.stop_advertising()
        logging.info("已停止")

if __name__ == "__main__":
    asyncio.run(main())
