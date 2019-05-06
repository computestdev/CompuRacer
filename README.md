# CompuRacer CLI toolset
The CompuRacer toolset for detection and exploitation of race conditions in web apps from a black-box perspective <sup id="a1">[1](#f1)</sup>. It integrates with the popular Burp Suite, and the browsers Firefox and Chrome to receive HTTP requests of interest. These requests are then sent to the core application. In the core application, these requests can be forwarded to the target web app to trigger a race condition. Responses from the web app are aggregated and shown to the tester to quick verification of success. This README shows how to install, setup and run the CompuRacer toolset.

The toolset can be split in three separate parts: Core application in `CompuRacerCore`, Burp extension in `CompuRacerExtensionBurp` and browser extensions (Chrome & Firefox) in `CompuRacerExtensionChrome` and `CompuRacerExtensionFirefox`. The `TestWebAppVouchers` folder contains a Flask test web app for voucher redemption that contains race conditions.

## Recommended software versions
The toolset has been tested with Python 3.7, Firefox v. 65, Chrome v. 72, Burp Suite Professional v1.7.37 and Vagrant 2.1.5. It is run on a MacBook Pro (late 2013) running macOS High Sierra. Every individual tool is expected to be compatible with Linux and Windows as well, but this is not tested. The plugin is also expected to work in Burp Suite CE.

## Installation
#### Clone the repository
`$ git clone https://github.com/rvemous/CompuRacer`
#### Install CompuRacer Core dependencies
* Go to the [`CompuRacer_Core/`](CompuRacer_Core/) folder.
* Run: `$ pip install -r requirements.txt`  
#### Install CompuRacer Firefox extension <br>
Firefox does not support adding extensions permanently if they are not signed by Mozilla. You can add it temporarily (until the next restart), using the following method:
* In Firefox, go to: Settings > Add-ons.
* Click the gear icon and select: `Debug Add-ons`.
* Go the [`CompuRacer_Extensions/Browser/Firefox/`](CompuRacer_Extensions/Browser/Firefox/) folder and select: `manifest.json`.
#### Install CompuRacer Chrome extension <br>
Note that due to recent changes in Chrome (after version 71), this extension will no longer send most of the headers to the CompuRacer. Therefore, in any authenticated session, it no longer works. You can add the extension using the following method:
* In Chrome, go to: Settings > More Tools > Extensions.
* Click: `Load unpacked`.
* Select the [`CompuRacer_Extensions/Browser/Chrome/`](CompuRacer_Extensions/Browser/Chrome/) folder.
#### Install CompuRacer Burp Suite extension
* In the Burp Suite, go to: Extender > Add.
* Select `Python` as the extension type.
* Go to the [`CompuRacer_Extensions/Burp/`](CompuRacer_Extensions/Burp/) folder and select: `compu_racer_extension_burp.py`.
* Click 'next' and after loading the extension, close the window.
#### Install test web app for voucher redemption
* In a terminal, go to the [`TestWebAppVouchers/app/`](TestWebAppVouchers/app/) folder.
* Run the following command: `vagrant up`.

## Configuration
The Firefox, Chrome, Burp Suite extensions and test web app do not need any configuration and are ready to use. The Computest Core will create the necessary folders and settings-files on the first startup. Make sure it has full read/write access rights in this folder.

## Running
The Firefox, Chrome, Burp Suite extensions and test web app are already started after the install. The Computest Core can be started by running the following command within the `CompuRacer_Core` folder: <br>
`$ python3 main.py`

## How to use
An elaborate manual on how to use the toolset can be found in [`CompuRacer_Manual.pdf`](CompuRacer_Manual.pdf).

## Troubleshooting
All extensions can be reloaded (or re-installed) if they stop working for one reason or another. All platforms support some form of (live) debugging of extensions. Report any found issues (and solutions) and these will be added here. 

### References
<b id="f1">1: </b>The toolset is a result of the master thesis "Towards Systematic Black-Box Testing for Exploitable Race Conditions in Web Apps" [↩](#a1) 
