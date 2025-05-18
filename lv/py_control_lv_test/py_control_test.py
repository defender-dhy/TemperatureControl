import pythoncom
import win32com.client
from win32com.client import VARIANT

labview = win32com.client.Dispatch("Labview.Application")

VI = labview.getvireference(r'E:\_team_proj\temp_control\my_labview\my_labview_projs\py_control_test\py_control_test.vi')

# VI.setcontrolvalue('NumericControl','5')
# VI.setcontrolvalue('BooleanSwitch','True')
VI.setcontrolvalue('StringControl','DHY DSB')

# 2. 定义你的 PID Gains（以两个浮点数为例）
Kp_value = 1.234    # 比如比例增益 Kp
Ti_value = 0.567    # 比如积分增益 Ti
Td_value = 0.890    # 比如积分增益 Td

raw_tuple = VI.GetControlValue("PIDGains")   # ← 一行搞定
print(raw_tuple)           # 结果往往是 (1.234, 0.567, 0.890)

raw_tuple = VI.SetControlValue("PIDGains", (Kp_value, Ti_value, Td_value))   # ← 一行搞定