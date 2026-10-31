const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('omni', {
  request: (op, data) => ipcRenderer.invoke('omni:request', op, data),
  screenshot: () => ipcRenderer.invoke('omni:screenshot'),
  window: action => ipcRenderer.send('omni:window', action)
});
