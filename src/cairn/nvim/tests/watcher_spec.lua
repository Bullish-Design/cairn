local function reload(name)
  package.loaded[name] = nil
  return require(name)
end

describe('cairn watcher parsing and REVIEWING detection', function()
  local tmp_home
  local ghost_calls

  before_each(function()
    tmp_home = vim.fn.tempname()
    vim.fn.mkdir(tmp_home .. '/state', 'p')
    vim.fn.mkdir(tmp_home .. '/previews', 'p')

    ghost_calls = {}
    package.loaded['cairn.ghost'] = {
      show = function(bufnr, agent_id, changes)
        table.insert(ghost_calls, { bufnr = bufnr, agent_id = agent_id, changes = changes })
      end,
    }
  end)

  it('parses unified diff additions with correct target lines', function()
    local watcher = reload('cairn.watcher')
    local parsed = watcher.parse_diff({
      'diff --git a/app.py b/app.py',
      '--- a/app.py',
      '+++ b/app.py',
      '@@ -1,2 +1,3 @@',
      ' line one',
      '+line two',
      '+line three',
    })

    assert.are.equal('line two', parsed['app.py'][1].text)
    assert.are.equal(2, parsed['app.py'][1].line)
    assert.are.equal(3, parsed['app.py'][2].line)
  end)

  it('detects REVIEWING transitions once per state and resets on exit', function()
    local watcher = reload('cairn.watcher')
    watcher.setup({ cairn_home = tmp_home, ghost_text = true })

    vim.fn.writefile({ vim.fn.json_encode({ ['agent-1'] = { state = 'REVIEWING' } }) }, tmp_home .. '/state/active_agents.json')
    vim.fn.writefile({
      'diff --git a/app.py b/app.py',
      '--- a/app.py',
      '+++ b/app.py',
      '@@ -0,0 +1,1 @@',
      '+hello',
    }, tmp_home .. '/previews/agent-1.diff')

    watcher.check_for_updates()
    watcher.check_for_updates()
    assert.are.equal(1, #ghost_calls)

    vim.fn.writefile({ vim.fn.json_encode({ ['agent-1'] = { state = 'RUNNING' } }) }, tmp_home .. '/state/active_agents.json')
    watcher.check_for_updates()

    vim.fn.writefile({ vim.fn.json_encode({ ['agent-1'] = { state = 'REVIEWING' } }) }, tmp_home .. '/state/active_agents.json')
    watcher.check_for_updates()
    assert.are.equal(2, #ghost_calls)
  end)
end)
