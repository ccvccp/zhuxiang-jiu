// 微信授权弹窗硬件允许器 — USB HID 鼠标
// 板卡: Arduino Leonardo / Pro Micro (ATmega32U4, 原生 USB HID)
// 协议(串口 115200, \n 结尾):
//   P  → 回 PONG    (握手探测)
//   H  → hover+click (微移动产生真实 WM_MOUSEMOVE → press 90ms → release)
//   C  → click      (纯左键单击, 在当前光标位置)
//   M dx dy → 相对移动
// 铁律: 命令驱动, 绝不自动乱点; 拟人时序(delay)防行为检测
// 合规: 外设级硬件输入(与人工点按系统层面等价), 非协议逆向/内存注入
//
// 烧录: Arduino IDE → 板卡选 Arduino Leonardo → 端口选 COMx → 上传
// 部署: 插入 bot 电脑 USB 口即生效(Win10/11 自动识别 HID+串口)

#include <Mouse.h>

const int BTN_PIN = 2;   // 可选: 物理按钮 D2→按钮→GND (按下=hover+click, 现场调试用)
bool btnWasDown = false;

void humanHover() {
  // 真实硬件 WM_MOUSEMOVE: 微移 1px 往返 (hover 进入, 拟人节奏)
  Mouse.move(1, 0);  delay(30);
  Mouse.move(-1, 0); delay(40);
}

void humanClick() {
  Mouse.press(MOUSE_LEFT);   // press/release 分离, 90ms 按住 = 人手节奏
  delay(90);
  Mouse.release(MOUSE_LEFT);
}

void setup() {
  Serial.begin(115200);
  Mouse.begin();
  pinMode(BTN_PIN, INPUT_PULLUP);
  // 32U4 CDC: PC 打开串口触发板子复位, setup 尾部给 900ms 让 PC 侧握手
  delay(900);
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "P") {
      Serial.println("PONG");
    } else if (cmd == "H") {
      humanHover();
      humanClick();
      Serial.println("OK_H");
    } else if (cmd == "C") {
      humanClick();
      Serial.println("OK_C");
    } else if (cmd.startsWith("M")) {
      int sp1 = cmd.indexOf(' ');
      int sp2 = cmd.indexOf(' ', sp1 + 1);
      if (sp1 > 0 && sp2 > sp1) {
        int dx = cmd.substring(sp1 + 1, sp2).toInt();
        int dy = cmd.substring(sp2 + 1).toInt();
        Mouse.move(dx, dy);
        Serial.println("OK_M");
      }
    }
  }
  // 可选物理按钮 (低电平触发)
  bool down = digitalRead(BTN_PIN) == LOW;
  if (down && !btnWasDown) { humanHover(); humanClick(); }
  btnWasDown = down;
}
