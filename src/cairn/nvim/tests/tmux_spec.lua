local function reload(name)
  package.loaded[name] = nil
  return require(name)
end

describe('cairn tmux preview behavior', function()
  local orig_system
  local orig_tmux
  local tmp_home

  before_each(function()
    tmp_home = vim.fn.tempname()
    vim.fn.mkdir(tmp_home .. '/workspaces/agent-123', 'p')
    vim.fn.mkdir(tmp_home .. '/workspaces/agent-123/src', 'p')
    vim.fn.writefile({ 'print("hello")' }, tmp_home .. '/workspaces/agent-123/src/main.py')

    package.loaded['cairn.config'] = nil
    package.loaded['cairn'] = nil
    local cairn = require('cairn')
    cairn.setup({
      cairn_home = tmp_home,
      keymaps = { accept = false, reject = false, preview = false },
    })

    orig_system = vim.fn.system
    orig_tmux = vim.env.TMUX
  end)

  after_each(function()
    vim.fn.system = orig_system
    vim.env.TMUX = orig_tmux
  end)

  it('creates preview pane when one does not exist', function()
    local calls = {}
    vim.fn.system = function(cmd)
      table.insert(calls, cmd)
      if cmd:find('list%-panes') then
        return ''
      end
      return 'ok'
    end

    vim.env.TMUX = 'session'
    reload('cairn.tmux').open_preview('agent-123', 'src/main.py', 12)

    assert.is_truthy(vim.tbl_contains(calls, 'tmux list-panes -F "#{pane_title}" | grep "^cairn-preview$"'))
    local split_cmd = vim.tbl_filter(function(cmd)
      return cmd:find('tmux split-window', 1, true) ~= nil
    end, calls)
    assert.are.equal(1, #split_cmd)
  end)

  it('reuses preview pane when it already exists', function()
    local calls = {}
    vim.fn.system = function(cmd)
      table.insert(calls, cmd)
      if cmd:find('list%-panes') then
        return 'cairn-preview'
      end
      return 'ok'
    end

    vim.env.TMUX = 'session'
    reload('cairn.tmux').open_preview('agent-123', 'src/main.py', 12)

    local send_cmd = vim.tbl_filter(function(cmd)
      return cmd:find('tmux send-keys', 1, true) ~= nil
    end, calls)
    assert.are.equal(1, #send_cmd)
  end)
end)
