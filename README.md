# CompuRacer CLI toolset
The CompuRacer toolset for detection and exploitation of race conditions in web apps from a black-box perspective <sup id="a1">[1](#f1)</sup>. It integrates with the popular Burp Suite, and the browsers Firefox and Chrome to receive HTTP requests of interest. These requests are then sent to the core application. In the core application, these requests can be forwarded to the target web app to trigger a race condition. Responses from the web app are aggregated and shown to the tester for quick verification of success. This README shows how to install, setup and run the CompuRacer toolset.

The toolset can be split in three separate parts: Core application in `CompuRacerCore`, Burp extension in `CompuRacerExtensionBurp` and browser extensions (Chrome & Firefox) in `CompuRacerExtensionChrome` and `CompuRacerExtensionFirefox`. The `TestWebAppVouchers` folder contains a Flask test web app for voucher redemption that contains race conditions.

## Recommended software versions
The toolset is only compatible with Python 3.7. It has been tested using Burp Suite Professional v1.7.37 & v2.1.03 (the Community Edition is also compatible), Firefox v. 69, Chrome v. 76 and Vagrant 2.1.5. It is tested on a MacBook Pro (2018) running macOS Mojave. Every individual tool is expected to be compatible with both Linux and Windows, but this is not fully tested.

## Installation
#### Clone the repository
`$ git clone https://github.com/computestdev/CompuRacer`
#### Install CompuRacer Core dependencies
* Go to the [`CompuRacer_Core/`](CompuRacer_Core/) folder.
* Run: `$ pip3 install -r requirements.txt`  
#### Install CompuRacer Burp Suite extension
* First, download the Jython standalone JAR file at https://www.jython.org/download and install the Requests library dependancy using: `$ pip3 install requests`.
* In the Burp Suite, go to: Extender > Options > Python Environment and select the downloaded JAR file.
* Then, point to the folder where the Requests library is installed. On a mac, this is probably: `/Library/Frameworks/Python.framework/Versions/3.7/lib/python3.7/site-packages`.
* Next, go to: Extender > Extensions > Add and select `Python` as the extension type.
* Regarding the extension file, go to the [`CompuRacer_Extensions/Burp/`](CompuRacer_Extensions/Burp/) folder and select: `compu_racer_extension_burp.py`.
* Click 'next' and after loading the extension, close the window.
#### Install CompuRacer Firefox extension (optional) 
Firefox does not support adding extensions permanently if they are not signed by Mozilla. You can add it temporarily (until the next restart), using the following method:
* In Firefox, go to: Settings > Add-ons.
* Click the gear icon and select: `Debug Add-ons`.
* Go the [`CompuRacer_Extensions/Browser/Firefox/`](CompuRacer_Extensions/Browser/Firefox/) folder and select: `manifest.json`.
#### Install CompuRacer Chrome extension (optional) 
Note that due to recent changes in Chrome (after version 71), this extension will no longer send most of the headers to the CompuRacer. Therefore, in any authenticated session, it no longer works. You can still add the extension using the following method:
* In Chrome, go to: Settings > More Tools > Extensions.
* Click: `Load unpacked`.
* Select the [`CompuRacer_Extensions/Browser/Chrome/`](CompuRacer_Extensions/Browser/Chrome/) folder.
#### Install test web app for voucher redemption (optional)
* In a terminal, go to the [`TestWebAppVouchers/app/`](TestWebAppVouchers/app/) folder.
* Run the following command: `vagrant up`.

## Configuration
The Firefox, Chrome, Burp Suite extensions and test web app do not need any configuration and are ready to use. The Computest Core will create the necessary folders and settings-files on the first startup. Make sure it has full read/write access rights in this folder.

## Running
The Firefox, Chrome, Burp Suite extensions and test web app are already started after the install. The Computest Core can be started by running the following command within the `CompuRacer_Core` folder: <br>
`$ python3.7 main.py [-h] [--port [PORT]] [--proxy [PROXY]]`

## How to use
An elaborate manual on how to use the toolset can be found in [`CompuRacer_Manual.pdf`](CompuRacer_Manual.pdf).

## Troubleshooting
All extensions can be reloaded (or re-installed) if they stop working for one reason or another. All platforms support some form of (live) debugging of extensions. Report any found issues (and solutions) and these will be added here. 

### References
<b id="f1">1: </b>Version 1.0 of the toolset is a result of the master thesis "Towards Systematic Black-Box Testing for Exploitable Race Conditions in Web Apps" [↩](#a1) 
