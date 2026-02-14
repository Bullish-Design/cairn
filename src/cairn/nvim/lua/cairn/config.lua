local M = {}

M.defaults = {
  cairn_home = vim.fn.expand('~/.cairn'),
  preview_same_location = true,
  auto_reload = false,
  ghost_text = true,
  keymaps = {
    accept = '<leader>a',
    reject = '<leader>r',
    preview = '<leader>p',
  },
}

M.values = vim.deepcopy(M.defaults)

function M.setup(opts)
  M.values = vim.tbl_deep_extend('force', vim.deepcopy(M.defaults), opts or {})
  return M.values
end

return M
