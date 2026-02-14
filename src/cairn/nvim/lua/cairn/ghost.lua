local M = {}

M.ns = vim.api.nvim_create_namespace('cairn_ghost')

function M.show(bufnr, agent_id, changes)
  vim.api.nvim_buf_clear_namespace(bufnr, M.ns, 0, -1)

  local buf_name = vim.api.nvim_buf_get_name(bufnr)
  local rel_path = vim.fn.fnamemodify(buf_name, ':.')

  local file_changes = changes[rel_path]
  if not file_changes then
    return
  end

  for _, change in ipairs(file_changes) do
    if change.type == 'add' then
      local line = math.max((change.line or 1) - 1, 0)
      vim.api.nvim_buf_set_extmark(bufnr, M.ns, line, 0, {
        virt_lines = { { { string.format(' + %s', change.text), 'Comment' } } },
        virt_lines_above = false,
      })
    end
  end

  vim.notify(
    string.format(
      'Agent %s has suggestions (press %s to preview)',
      agent_id:sub(1, 8),
      require('cairn').config.keymaps.preview or '<leader>p'
    ),
    vim.log.levels.INFO
  )
end

function M.clear(bufnr)
  vim.api.nvim_buf_clear_namespace(bufnr, M.ns, 0, -1)
end

return M
