#include "arg.h"
#include "common.h"
#include "llama.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

struct dump_cb_user_data {
    std::vector<std::string> target_names;
    std::string              out_dir;
    int                      n_layers = 0;
    std::map<std::string, int> counters;
};

static bool tensor_name_matches(const char * name, const dump_cb_user_data * data) {
    for (const auto & prefix : data->target_names) {
        if (std::strstr(name, prefix.c_str()) != nullptr) {
            return true;
        }
    }
    return false;
}

static bool dump_hidden_states_cb(struct ggml_tensor * t, bool ask, void * user_data) {
    auto * data = static_cast<dump_cb_user_data *>(user_data);

    if (ask) {
        return tensor_name_matches(t->name, data);
    }

    if (!tensor_name_matches(t->name, data)) {
        return true;
    }

    if (t->type != GGML_TYPE_F32) {
        fprintf(stderr, "skip %s: type %s is not F32\n", t->name, ggml_type_name(t->type));
        return true;
    }

    const size_t n_bytes = ggml_nbytes(t);
    std::vector<uint8_t> tmp(n_bytes);
    ggml_backend_tensor_get(t, tmp.data(), 0, n_bytes);
    const uint8_t * raw = tmp.data();

    int cnt = data->counters[t->name]++;
    std::string base_name = std::string(t->name) + "_" + std::to_string(cnt);
    std::filesystem::path raw_path = std::filesystem::path(data->out_dir) / (base_name + ".raw");
    std::ofstream fout(raw_path, std::ios::binary);
    if (!fout) {
        fprintf(stderr, "failed to open %s for writing\n", raw_path.c_str());
        return false;
    }
    fout.write(reinterpret_cast<const char *>(raw), n_bytes);
    fout.close();

    std::filesystem::path json_path = std::filesystem::path(data->out_dir) / (base_name + ".json");
    std::ofstream jout(json_path);
    if (!jout) {
        fprintf(stderr, "failed to open %s for writing\n", json_path.c_str());
        return false;
    }
    jout << "{\n";
    jout << "  \"name\": \"" << t->name << "\",\n";
    jout << "  \"type\": \"" << ggml_type_name(t->type) << "\",\n";
    jout << "  \"ne\": [" << t->ne[0] << ", " << t->ne[1] << ", " << t->ne[2] << ", " << t->ne[3] << "],\n";
    jout << "  \"nb\": [" << t->nb[0] << ", " << t->nb[1] << ", " << t->nb[2] << ", " << t->nb[3] << "],\n";
    jout << "  \"n_elements\": " << ggml_nelements(t) << ",\n";
    jout << "  \"n_bytes\": " << n_bytes << "\n";
    jout << "}\n";
    jout.close();

    fprintf(stderr, "saved %s: ne=(%ld,%ld,%ld,%ld) n_elements=%ld n_bytes=%zu -> %s\n",
            t->name, t->ne[0], t->ne[1], t->ne[2], t->ne[3], ggml_nelements(t), n_bytes, raw_path.c_str());

    return true;
}

static bool run(llama_context * ctx, const common_params & params) {
    const llama_model * model = llama_get_model(ctx);
    const llama_vocab * vocab = llama_model_get_vocab(model);

    const bool add_bos = llama_vocab_get_add_bos(vocab);

    std::vector<llama_token> tokens = common_tokenize(ctx, params.prompt, add_bos, true);

    if (tokens.empty()) {
        fprintf(stderr, "%s: there are no input tokens to process - (try to provide a prompt with '-p')\n", __func__);
        return false;
    }

    fprintf(stderr, "number of input tokens = %zu\n", tokens.size());
    for (size_t i = 0; i < tokens.size(); ++i) {
        fprintf(stderr, "token[%zu] = %d\n", i, tokens[i]);
    }

    if (llama_decode(ctx, llama_batch_get_one(tokens.data(), tokens.size()))) {
        fprintf(stderr, "%s: failed to eval\n", __func__);
        return false;
    }

    return true;
}

int main(int argc, char ** argv) {
    common_params params;
    dump_cb_user_data cb_data;

    common_init();

    if (!common_params_parse(argc, argv, params, LLAMA_EXAMPLE_COMMON)) {
        return 1;
    }

    llama_backend_init();
    llama_numa_init(params.numa);

    cb_data.out_dir = "refs";
    cb_data.target_names = {"l_out", "attn_norm", "Qcur", "Kcur", "Vcur", "ffn_inp", "ffn_norm", "ffn_out", "ffn_gate", "ffn_up", "ffn_down", "inp_embd", "token_embd", "tok_embd"};
    cb_data.n_layers = params.n_predict > 0 ? params.n_predict : 2;

    std::filesystem::create_directories(cb_data.out_dir);

    params.cb_eval = dump_hidden_states_cb;
    params.cb_eval_user_data = &cb_data;
    params.warmup = false;

    auto llama_init = common_init_from_params(params);

    auto * model = llama_init->model();
    auto * ctx   = llama_init->context();

    if (model == nullptr || ctx == nullptr) {
        fprintf(stderr, "%s: failed to init\n", __func__);
        return 1;
    }

    if (!run(ctx, params)) {
        return 1;
    }

    llama_backend_free();

    return 0;
}
