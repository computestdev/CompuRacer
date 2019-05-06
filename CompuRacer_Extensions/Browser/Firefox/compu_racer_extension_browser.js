/*
MIT License

Copyright (c) 2019

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
 */

// CompuRacer browser extension

// Captures all requests when active and sends it to the picked_server for further analysis.

var port = "8099";
var server_options = ["http://localhost", "http://127.0.0.1"];
var server_picked = null;

var ignore_list = {};

var default_timeout = 5000;
var requests = {};
var patterns = ["http://*/*", "https://*/*"];
var on = false;

appState = 'disconnected';

// changes the state of the plugin to disconnected, connected idle and busy
// the color of the icon and the tooltip are updated and a log is written on disconnection
var updateState = function (newState, forceUpdate) {
    if (!forceUpdate && newState === appState) {
        return
    }
    if (newState === 'disconnected') {
        server_picked = null;
        chrome.browserAction.setIcon({path: 'icons/icon_disabled.png'});
        chrome.browserAction.setTitle({
            title: 'CompuRacer (disconnected)'
        });
        console.log("Cannot find a racer server. Click the extension button to retry..")
    } else if (newState === 'idle') {
        chrome.browserAction.setIcon({path: 'icons/icon_off.png'});
        chrome.browserAction.setTitle({
            title: 'CompuRacer (connected - idle)'
        });
    } else if (newState === 'busy') {
        chrome.browserAction.setIcon({path: 'icons/icon_on.png'});
        chrome.browserAction.setTitle({
            title: 'CompuRacer (connected - busy)'
        });
    }
    appState = newState
};

// Get racer server from options.
// If one is found, we stop looking for alternatives.
var fetcher = function (val, idx) {
    return new Promise(resolve => {
        fetch(val + ":" + port).then((r) => {
            resolve(r.status)
        }).catch((error) => resolve("400"));
    });
};
var results = Promise.all(server_options.map(fetcher));
results.then(data => {
    for (var i = 0; i < data.length; i++) {
        if (data[i] === 200) {
            server_picked = server_options[i] + ":" + port;
            console.log("SERVER: " + server_picked);
            break
        }
    }
    if (server_picked === null) {
        updateState('disconnected', true);
        return
    } else {
        updateState('idle', true)
    }
    return fetch(server_picked + '/ignore')
}).then((r) => {
    if (r === undefined) {
        return
    }
    r.json().then((data) => {
        ignore_list = data['urls'];
    });
}).catch(console.log.bind(console));

function ignore(details) {
    // check ignored extentions
    let ignored_extentions = [".ico", ".js", ".css", ".png", ".jpg", ".gif", ".svg", ".woff", "woff2", ".ttf"];
    let ignored_contents = ["favicon.ico"]
    for (var i = 0; i < ignored_extentions.length; i++) {
        if (details.url.endsWith(ignored_extentions[i])) {
            console.log("IGNORED (EXT): " + details.url);
            return true
        }
    }
    for (var i = 0; i < ignored_contents.length; i++) {
        if (details.url.includes(ignored_contents[i])) {
            console.log("IGNORED (CON): " + details.url);
            return true
        }
    }
    // GET could be included too
    // as there are a lot of state-changing GET requests around
    ignored_methods = ['OPTIONS', 'CONNECT'];
    if (details.url === server_picked + '/add_request' || ignored_methods.indexOf(details.method) !== -1) {
        console.log("IGNORED (M/U): " + details.url);
        return true
    } else {
        return false
    }
}

function PreRequest(details) {
    // check ignore list
    if (ignore(details)) return doReturn(false);
    // now, only requests to the same domain are allowed
    chrome.tabs.query({'active': true, 'lastFocusedWindow': true},
        function (tabs) {
            if (tabs[0] === undefined) {
                console.log("IGNORED (TAB): " + details.url);
                return doReturn(false)
            }
            var tab_domain = new URL(tabs[0].url).hostname;
            var query_domain = new URL(details.url).hostname;
            if (query_domain !== tab_domain) {
                console.log("IGNORED (DOM): " + details.url);
                return doReturn(false)
            }
            console.log("Active tab matches: " + tab_domain);
            return Request(details)
        }
    )
}

function Request(details) {
    requests[details.requestId] = {
        url: encodeURI(details.url),
        method: details.method,
    };

    if (details.requestBody) {
        if (details.requestBody.formData) {
            console.log(details.requestBody);
            form = details.requestBody.formData;
            body = '';
            for (var i in form) {
                body += encodeURIComponent(i) + '=' + encodeURIComponent(form[i][0]) + '&'
            }
        }
        else if (details.requestBody.raw) {
            body = new TextDecoder('utf-8').decode(details.requestBody.raw[0].bytes)
        }
        else if (typeof body === 'undefined') {
            body = {}
        }
        requests[details.requestId].body = body
    }
    console.log("ACCEPTED REQ: " + details.url);
    return doReturn(false)
}

// checks whether we should ignore the request headers based on the filter
// function and whether it comes from the active tab
function PreSendHeaders(details) {
    // check ignore list
    if (ignore(details)) return doReturn(false);
    // now, only requests to the same domain are allowed
    chrome.tabs.query({'active': true, 'lastFocusedWindow': true},
        function (tabs) {
            if (tabs[0] === undefined) {
                console.log("IGNORED (TAB): " + details.url);
                return doReturn(false)
            }
            var tab_domain = new URL(tabs[0].url).hostname;
            var query_domain = new URL(details.url).hostname;
            if (query_domain !== tab_domain) {
                console.log("IGNORED (DOM): " + details.url);
                return doReturn(false)
            }
            console.log("Active tab matches: " + tab_domain);
            return SendHeaders(details)
        }
    );
    return doReturn(true)
}

function SendHeaders(details) {
    // body creation was canceled
    if (requests[details.requestId] === undefined) {
        console.log("IGNORED (BODY)*: " + details.url + " id = " + details.requestId);
        //return doReturn(false)
        requests[details.requestId] = {
            url: details.url,
            method: details.method,
        }
    }
    console.log("SENDING: " + details.url);

    var headers = {};

    for (var i = 0; i < details.requestHeaders.length; ++i) {
        headers[details.requestHeaders[i].name] = details.requestHeaders[i].value
    }

    requests[details.requestId].headers = headers;
    //console.log(JSON.stringify(requests[details.requestId]))
    res = fetch(server_picked + '/add_request', {
        method: 'POST',
        headers: new Headers({
            'content-type': 'application/json',
            //'X-Requested-With': 'Browser extension'
        }),
        body: JSON.stringify(requests[details.requestId])
    }).then(data => {
        for (var i = 0; i < data.length; i++) {
            if (data[i] !== 200) {
                updateState('disconnected', false)
            }
        }
    }).catch((error) => {
        updateState('disconnected', false)
    });
    console.log("Blocked headers! " + requests[details.requestId]);
    return {cancel: true} //doReturn(true)
}

// Note: returning a onBeforeRequest with a cancel = true blocks the request, when it is false, it sends it through and when you use a redirectUrl property with a value of this URL, it redirects.
chrome.browserAction.onClicked.addListener(function (t) {
    if (server_picked != null) {
        updateState('busy', false);
        chrome.webRequest.onBeforeSendHeaders.addListener(PreSendHeaders, {urls: patterns}, ["blocking", "requestHeaders"]);
        chrome.webRequest.onBeforeRequest.addListener(PreRequest, {urls: patterns}, ['blocking', 'requestBody']);

        setTimeout(function () {
            chrome.webRequest.onBeforeRequest.removeListener(PreRequest);
            chrome.webRequest.onBeforeSendHeaders.removeListener(PreSendHeaders);
            if (server_picked != null) {
                updateState('idle', false)
            }
        }, default_timeout)
    } else {
        chrome.runtime.reload();
    }
});

