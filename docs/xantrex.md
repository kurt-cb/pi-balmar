On a Xantrex Freedom SW 3000 inverter/charger, the CAN_H and CAN_L communication pins are located inside the standard RJ45 (8-pin) Xanbus ports. Xantrex utilizes a CAN-based network protocol called Xanbus to link devices like the System Control Panel (SCP) or automatic generator starters. [1, 2, 3, 4, 5] 
## Xanbus RJ45 CAN Bus Pinout
The ports adhere to the standard T568A wiring profile. When looking directly at the front of an RJ45 modular plug with the release tab facing away from you (or looking into the female port), the pins are numbered 1 to 8 from left to right: [6, 7] 

* Pin 4: CAN_L (Conductor Name: CAN_L, typically the Solid Blue wire)
* Pin 5: CAN_H (Conductor Name: CAN_H, typically the White/Blue striped wire) [6, 7] 

## Full Port Reference
If you are building custom communication cables or integrating a third-party battery management system (BMS), it is critical to know what the surrounding pins do so you do not accidentally short out the system:

| Pin Number | Conductor Name | Description | Standard CAT5 Color (T568A) |
|---|---|---|---|
| 1 | NET_S | +15 VDC Network Power | White/Green |
| 2 | NET_S | +15 VDC Network Power | Green |
| 3 | NET_C | Network Common / Ground | White/Orange |
| 4 | CAN_L | CAN Bus Low Signal | Blue |
| 5 | CAN_H | CAN Bus High Signal | White/Blue |
| 6 | NET_C | Network Common / Ground | Orange |
| 7 | NET_S | +15 VDC Network Power | White/Brown |
| 8 | NET_C | Network Common / Ground | Brown |

## Important Installation Warnings

* Do Not Use Crossover Cables: Standard Xanbus networking requires straight-through CAT5 or CAT5e patch cables. A crossover cable can map the 15V power pins directly into the CAN communication lines, instantly destroying the networking board inside the inverter. [6, 8] 
* Termination Resistance: The network operates at a specific baud rate and requires a network terminator (a male or female RJ45 plug with a 120-ohm resistor across Pins 4 and 5) inserted into any empty Xanbus port at both ends of the physical network chain. [6, 9] 

Are you trying to connect a third-party lithium battery (BMS) to the Xantrex, or are you trying to read data using an Arduino/Raspberry Pi? If you tell me what you're connecting, I can help you with the protocol or wiring requirements.

[1] [https://xantrex.com](https://xantrex.com/products/accessories/freedom-sw-xanbus-automatic-generator-start/)
[2] [https://www.scribd.com](https://www.scribd.com/document/855740648/Book-Davide-Andrea-Lithium-Ion-Batteries-and-Applications-A-Practical-and-Comprehensive-Guide-to-Lithium-Ion-Batteries-Vol2)
[3] [https://xantrex.com](https://xantrex.com/products/accessories/freedom-sw-xanbus-automatic-generator-start/)
[4] [https://inverterservicecenter.com](https://inverterservicecenter.com/xanbus-system-control-panel-xantrex-809-0921)
[5] [https://www.donrowe.com](https://www.donrowe.com/Xantrex-808-9010-Freedom-SW-Remote-Adapter-p/808-9010.htm)
[6] [https://xantrex.com](https://xantrex.com/wp-content/uploads/2023/03/Xanbus_System_975-0136-01-01_IM.pdf)
[7] [https://studylib.net](https://studylib.net/doc/18301942/technical-note-powering-xanbus-network-through)
[8] [https://xantrex.com](https://xantrex.com/wp-content/uploads/2021/12/975-0731-01-01_Rev-C.pdf)
[9] [https://www.pilz.com](https://www.pilz.com/en-ZA/eshop/Connection-technology-and-education-systems/Connection-technology-and-education-systems/Cables-and-plug-in-connectors/Adapter/c/0011100248716980UK)
