local M = {}

local timer = nil
local seen_reviewing = {}

function M.setup(config)
  M.config = config
end

function M.start()
  if timer then
    return
  end

  timer = vim.loop.new_timer()
  timer:start(500, 500, vim.schedule_wrap(function()
    M.check_for_updates()
  end))
end

function M.stop()
  if timer then
    timer:stop()
    timer:close()
    timer = nil
  end
end

function M.check_for_updates()
  if not M.config then
    return
  end

  local state_file = M.config.cairn_home .. '/state/active_agents.json'
  if vim.fn.filereadable(state_file) == 0 then
    return
  end

  local agents = vim.fn.json_decode(vim.fn.readfile(state_file)) or {}

  for agent_id, info in pairs(agents) do
    if info.state == 'REVIEWING' and not seen_reviewing[agent_id] then
      local preview_file = M.config.cairn_home .. '/previews/' .. agent_id .. '.diff'
      if vim.fn.filereadable(preview_file) == 1 then
        local diff_content = vim.fn.readfile(preview_file)
        local changes = M.parse_diff(diff_content)
        local bufnr = vim.api.nvim_get_current_buf()

        if M.config.ghost_text then
          require('cairn.ghost').show(bufnr, agent_id, changes)
        end

        seen_reviewing[agent_id] = true
      end
    elseif info.state ~= 'REVIEWING' then
      seen_reviewing[agent_id] = nil
    end
  end
end

function M.parse_diff(diff_lines)
  local changes = {}
  local current_file = nil
  local new_line = 0

  for _, line in ipairs(diff_lines) do
    local file_match = line:match('^%+%+%+ b/(.+)$')
    if file_match then
      current_file = file_match
      changes[current_file] = changes[current_file] or {}
    else
      local new_start = line:match('^@@ %-%d+,?%d* %+(%d+),?%d* @@')
      if new_start then
        new_line = tonumber(new_start) or 0
      elseif current_file and line:sub(1, 1) == '+' and line:sub(1, 3) ~= '+++' then
        table.insert(changes[current_file], {
          type = 'add',
          line = new_line,
          text = line:sub(2),
        })
        new_line = new_line + 1
      elseif current_file and line:sub(1, 1) == '-' and line:sub(1, 3) ~= '---' then
        -- Removed line in new file: do not advance new_line.
      elseif current_file then
        if line:sub(1, 1) == ' ' or line == '' then
          new_line = new_line + 1
        end
      end
    end
  end

  return changes
end

return M
