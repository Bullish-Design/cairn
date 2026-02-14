local M = {}

local config = require('cairn.config')

M.config = config.values

function M.setup(opts)
  M.config = config.setup(opts)

  require('cairn.watcher').setup(M.config)

  if M.config.keymaps.accept then
    vim.keymap.set('n', M.config.keymaps.accept, function()
      require('cairn.commands').accept()
    end, { desc = 'Accept Cairn changes' })
  end

  if M.config.keymaps.reject then
    vim.keymap.set('n', M.config.keymaps.reject, function()
      require('cairn.commands').reject()
    end, { desc = 'Reject Cairn changes' })
  end

  if M.config.keymaps.preview then
    vim.keymap.set('n', M.config.keymaps.preview, function()
      require('cairn.commands').preview()
    end, { desc = 'Open Cairn preview' })
  end

  require('cairn.watcher').start()
end

return M
