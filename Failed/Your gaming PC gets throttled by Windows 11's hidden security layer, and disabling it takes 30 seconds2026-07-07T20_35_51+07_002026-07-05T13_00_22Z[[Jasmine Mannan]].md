---
title: "Your gaming PC gets throttled by Windows 11's hidden security layer, and disabling it takes 30 seconds"
source: "https://www.xda-developers.com/gaming-pc-throttled-windows-11-hidden-security-disabling-takes-seconds/?link_source=ta_first_comment&taid=6a4a94a4ad50b40001f9ff58&utm_campaign=trueanthem&utm_medium=social&utm_source=facebook"
author:
  - "[[Jasmine Mannan]]"
published: 2026-07-05
created: 2026-07-07
description: "In-game stuttering is an issue of the past"
tags:
  - "clippings"
---
When building a high-end gaming rig, you expect buttery-smooth performance. However, many users might face baffling microstutters and erratic frame pacing in heavy modern open-world games. [The culprit isn't your hardware;](https://www.xda-developers.com/signs-your-gpu-isnt-responsible-for-fps-issues/) it's core isolation and virtualization-based security, which Microsoft quietly turns on by default [in a clean Windows 11 installation](https://www.xda-developers.com/debloating-windows-11-handheld-gaming/).

Windows 11 treats your gaming PC like a corporate office workstation. By forcing memory protection into a separate virtualized container, the operating system introduces a severe scheduling penalty on the CPU. Disabling core isolation doesn't magically boost your average FPS by hundreds, but it does stop the sudden jarring frame drops that ruin fluid gameplay.

## VBS might be ruining your games

### Stuttering is frustrating

![An RTX 4070 Ti gaming PC.](https://static0.xdaimages.com/wordpress/wp-content/uploads/wm/2026/05/nvidia-rtx-4070-ti-gaming-pc.jpg?q=49&fit=crop&w=825&dpr=2)

Let's say you have a [flagship PC with great RAM](https://www.xda-developers.com/ram-bottleneck-signs/) and your average frame-rate counter showing a glorious 120 FPS at 4K. For some reason, when you're playing, the game still feels choppy, and every time you turn a corner or sprint through a crowded area, the game momentarily hitches. Even if you lower graphics settings or have tried wiping drivers with DDU and toggling G-Sync, the micro-stutter persists.

An issue you might be ignoring is memory integrity. By switching off memory integrity you may find that yoru average FPS stays the same but your 1% low metrics skyrocket by 15 to 25%. The stuttering completely vanishes.

The reason behind this is because of virtualization-based security and hypervisor-protected code integrity. VBS is an architecture that Windows 11 uses to create an isolated, secure region of memory entirely separate from the main operating system. This means it treats your core operating system like an untrusted guest. HVCI is a specific feature underneath Core Isolation. It uses a virtualized sandbox created by VBS to verify that all kernel-mode drivers are securely signed before they can execute code.

But what does this actually mean for gaming? Games require lightning-fast, direct, low-latency communication between the game engine, the graphics driver, and your silicon within your PC itself. When HVCI is active, every driver call must traverse a virtualization layer to be vetted inside the secure enclave. This introduces structural CPU latency spikes, which is exactly the kind of thing that can cause micro-stutters in games.

## How to switch off memory integrity

### Reclaim your performance

In order to reclaim your full silicone performance, you can safely disable the virtualization overhead by following the directions below. The first thing to do is to actually locate your core isolation settings by clicking on the Windows Start menu, typing **Windows Security**, and then hitting Enter. Navigate to the **Device Security** tab on the left sidebar and click on the **Core Isolation Details** hyperlink at the top of the interface.

Once you're here, it's time to toggle off the **memory integrity** option. There should be a toggle switch. Ensure you flip it to **Off**. Windows will throw an administrative user account control prompt asking for confirmation, and be sure you approve the change. Before any changes take place, you'll have to restart your system.

Once your PC launches back up, it's best to actually verify the VBS state profile before proceeding. Once back on your desktop, press **Win + R** and then type **msinfo32** and press Enter to launch System Information. Scroll to the bottom of the summary list and confirm that virtualization-based security is explicitly marked as **not enabled**. If this isn't the case, go back and repeat the steps again to make sure that it is.

## Your average FPS may not change

### But you'll still notice the difference

![Forza Horizon 6 and a pink Chevy Corvette on a gaming PC.](https://static0.xdaimages.com/wordpress/wp-content/uploads/wm/2026/05/forza-horizon-6-on-a-geforce-gtx-1660-ti-gaming-pc.jpg?q=49&fit=crop&w=825&dpr=2)

When you adjust these settings, you might notice that your average FPS doesn't really change all that much, but this still makes a major difference to your gameplay experience. Standard benchmarks usually hide stuttering issues, as a game running at 100 FPS average can still feel like a jittery mess if it's got constant 1% lows dropping down to 30 FPS.

When Core Isolation is switched on, the hardware virtualization layer randomly delays driver execution threads to the gamer. This registers as a sudden, jarring spike in frame time, causing input lag and immediate visual stuttering.

It's worth keeping in mind that if you are adjusting these settings, you should know what you're getting yourself into. Disabling Core Isolation does remove a layer of enterprise-grade armor designed to stop sophisticated kernel-level exploits like malware that tries to hijack low-level hardware drivers. If you're switching this off, you have to ensure that you're ready to risk that level of security.

For many, it might be worth the risk. It's worth weighing up your personal circumstances to decide. If your PC is primarily a personal gaming rig, you have a secure home router firewall, you don't download shady executable files from unvetted sites, and you keep Windows Defender active, the threat vector is remarkably small, but that doesn't mean it's completely obsolete. For enthusiasts trading an extreme corporate defense layer for silky smooth strata-free gameplay, it might be worth it, but just know you are taking a risk.

## A gaming PC requires extra care

### Using the same settings as a corporate desktop won't suffice

Microsoft engineered Windows 11 to be secure by default for millions of corporate laptops deployed across enterprise networks, but for a custom-built gaming PC, it's an entirely different animal that demands raw, unthrottled thread execution. Stop troubleshooting game patches, swapping display cables, or blaming your graphics card for random micro stutters. Take 30 seconds to disable Core Isolation, reboot your system, strip away the invisible virtualization tags, and finally let your CPU deliver the flawless, unthrottled frame pacing you actually paid for.