local M = {}

local function run(cmd)
  return vim.fn.system(cmd)
end

function M.open_preview(agent_id, file_path, line_num)
  local config = require('cairn').config
  local workspace = config.cairn_home .. '/workspaces/' .. agent_id

  if vim.fn.isdirectory(workspace) == 0 then
    vim.notify('Workspace not materialized yet', vim.log.levels.WARN)
    return
  end

  if vim.env.TMUX == nil or vim.env.TMUX == '' then
    vim.notify('TMUX session not detected', vim.log.levels.WARN)
    return
  end

  local target_file = workspace .. '/' .. file_path
  if vim.fn.filereadable(target_file) == 0 then
    target_file = workspace
  end

  local nvim_cmd
  if vim.fn.filereadable(target_file) == 1 and config.preview_same_location then
    nvim_cmd = string.format('nvim +%d %s', line_num, vim.fn.shellescape(target_file))
  else
    nvim_cmd = string.format('nvim %s', vim.fn.shellescape(target_file))
  end

  local preview_exists = run('tmux list-panes -F "#{pane_title}" | grep "^cairn-preview$"')

  if preview_exists ~= '' then
    run(string.format('tmux send-keys -t cairn-preview C-c "%s" Enter', nvim_cmd))
    run('tmux select-pane -t cairn-preview')
  else
    run(string.format(
      'tmux split-window -h -c %s "tmux select-pane -T cairn-preview; %s"',
      vim.fn.shellescape(workspace),
      nvim_cmd
    ))
  end

  vim.notify('Preview opened in TMUX', vim.log.levels.INFO)
end

function M.close_preview()
  run('tmux kill-pane -t cairn-preview')
end

return M
