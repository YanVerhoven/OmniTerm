const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const { Simulator } = require('./simulator.cjs');

let simulator;
function createWindow() {
  const win = new BrowserWindow({
    width: 1320, height: 850, minWidth: 850, minHeight: 600,
    backgroundColor: '#0d0f12', title: 'OmniTerm', frame: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, nodeIntegration: false }
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
}

app.whenReady().then(() => {
  simulator = new Simulator(path.join(app.getPath('userData'), 'omniterm.json'));
  setInterval(() => simulator.handle('cleanup'), 60_000);
  createWindow();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); });
ipcMain.handle('omni:request', (_event, op, data) => simulator.handle(op, data || {}));
ipcMain.on('omni:window', (event, action) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return;
  if (action === 'minimize') win.minimize();
  if (action === 'maximize') win.isMaximized() ? win.unmaximize() : win.maximize();
  if (action === 'close') win.close();
});
